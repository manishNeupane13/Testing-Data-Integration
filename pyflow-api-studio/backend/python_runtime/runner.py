import asyncio
import json
import os
from uuid import uuid4
from pydantic import BaseModel, Field

class PythonConfig(BaseModel):
    code: str = Field(min_length=1,max_length=50000)
    timeout: int = Field(5,ge=1,le=30)
    memory_mb: int = Field(128,ge=64,le=512)

class PythonExecutionError(ValueError): pass

# This code executes inside an isolated, disposable container, never in the API process.
WORKER = r'''
import json, sys, io, contextlib, traceback
payload=json.load(sys.stdin)
class Log(io.StringIO):
    def write(self,text):
        if self.tell()+len(text)>65536: raise ValueError('Printed output exceeds 64 KiB')
        return super().write(text)
log=Log()
try:
    scope={'data':payload['data'],'__name__':'__custom_python__'}
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        exec(compile(payload['code'],'<custom-python>','exec'),scope)
    if 'result' not in scope: raise ValueError('Assign your output to the variable result')
    encoded=json.dumps({'data':scope['result'],'stdout':log.getvalue()},allow_nan=False)
    if len(encoded.encode())>2097152: raise ValueError('Result exceeds 2 MiB')
    print(encoded)
except BaseException:
    print(json.dumps({'error':traceback.format_exc(limit=8),'stdout':log.getvalue()}))
    sys.exit(1)
'''

class PythonRunner:
    OUTPUT_LIMIT=2*1024*1024+65536
    def __init__(self):
        self.image=os.getenv('PYFLOW_PYTHON_IMAGE','pyflow-python:1')
        self.slots=asyncio.Semaphore(2)

    async def status(self):
        try:
            p=await asyncio.create_subprocess_exec('docker','image','inspect',self.image,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            try: ready=await asyncio.wait_for(p.wait(),3)==0
            except TimeoutError: p.kill();await p.wait();ready=False
        except FileNotFoundError: ready=False
        setup='docker build -t pyflow-python:1 runtime' if self.image=='pyflow-python:1' else f'docker pull {self.image}'
        return {'ready':ready,'image':self.image,'setup':'Start Docker Desktop, then run: '+setup,'network':False,'max_seconds':30,'max_memory_mb':512}

    async def _cleanup(self,name,process):
        try:
            cleanup=await asyncio.create_subprocess_exec('docker','rm','-f',name,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            try: await asyncio.wait_for(cleanup.wait(),5)
            except TimeoutError: cleanup.kill();await cleanup.wait()
        finally:
            if process.returncode is None:
                process.kill();await process.wait()

    async def execute(self,config,data):
        config=PythonConfig.model_validate(config)
        payload=json.dumps({'code':config.code,'data':data},allow_nan=False).encode()
        if len(payload)>10*1024*1024: raise PythonExecutionError('Input exceeds 10 MiB')
        async with self.slots:
            status=await self.status()
            if not status['ready']: raise PythonExecutionError('Python runtime unavailable. '+status['setup'])
            name='pyflow-code-'+str(uuid4())
            args=['docker','run','--rm','--pull=never','--name',name,'--network=none','--read-only',
                  '--cap-drop=ALL','--security-opt=no-new-privileges','--user=65534:65534',
                  '--memory='+str(config.memory_mb)+'m','--memory-swap='+str(config.memory_mb)+'m',
                  '--cpus=1','--pids-limit=32','--ulimit=nofile=64:64',
                  '--tmpfs=/tmp:rw,noexec,nosuid,size=16m','--log-driver=none','-i',self.image,'python','-I','-B','-c',WORKER]
            process=await asyncio.create_subprocess_exec(*args,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            async def read_bounded(stream):
                chunks=[];size=0
                while chunk:=await stream.read(16384):
                    size+=len(chunk)
                    if size>self.OUTPUT_LIMIT: raise PythonExecutionError('Python output exceeds limit')
                    chunks.append(chunk)
                return b''.join(chunks)
            async def send():
                try:
                    process.stdin.write(payload);await process.stdin.drain()
                except (BrokenPipeError,ConnectionResetError): pass
                finally: process.stdin.close()
            tasks=[asyncio.create_task(send()),asyncio.create_task(read_bounded(process.stdout)),asyncio.create_task(read_bounded(process.stderr)),asyncio.create_task(process.wait())]
            try:
                _,stdout,stderr,code=await asyncio.wait_for(asyncio.gather(*tasks),config.timeout+2)
                if code:
                    try: detail=json.loads(stdout).get('error')
                    except (ValueError,TypeError): detail=None
                    raise PythonExecutionError(detail or stderr.decode(errors='replace')[:4000] or f'Python process exited {code} (possibly a memory limit)')
                try: output=json.loads(stdout)
                except ValueError as exc: raise PythonExecutionError('Python did not return valid JSON') from exc
                if not isinstance(output,dict) or 'data' not in output: raise PythonExecutionError('Missing Python result')
                return output
            except TimeoutError as exc: raise PythonExecutionError('Python execution time limit exceeded') from exc
            finally:
                for task in tasks:
                    if not task.done(): task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
                await asyncio.shield(self._cleanup(name,process))
