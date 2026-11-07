import os
import asyncio
import pytest
from pydantic import ValidationError
from backend.python_runtime.runner import PythonConfig,PythonRunner,PythonExecutionError
from backend.models.config import Node,Workflow
from backend.workflows.engine import WorkflowEngine
from backend.storage.repository import Repository
from test_platform import executor

@pytest.mark.parametrize('config',[{'code':''},{'code':'result=data','timeout':31},{'code':'result=data','memory_mb':1}])
def test_python_config_limits(config):
    with pytest.raises(ValidationError): Node(id='code',type='python',config=config)

async def test_python_node_references_and_persistence(tmp_path):
    http=executor();repo=Repository(tmp_path);engine=WorkflowEngine(repo,http)
    async def run(config,data):
        assert data==[{'id':1}]
        assert config['code']=="result = '{{ nodes.start.output.data }}'"
        return {'data':[{'valid':True}],'stdout':'checked'}
    engine.python.execute=run
    workflow=Workflow(name='code',nodes=[Node(id='start',type='manual'),Node(id='python',type='python',dependencies=['start'],config={'code':"result = '{{ nodes.start.output.data }}'"})])
    result=await engine.execute(workflow,'run',[{'id':1}])
    assert result['nodes']['python']['output']['data']==[{'valid':True}]
    assert repo.get('node_outputs','run_python')['output']['stdout']=='checked'
    await http.close()

async def test_runtime_unavailable(monkeypatch):
    runner=PythonRunner()
    async def unavailable(): return {'ready':False,'setup':'Start Docker'}
    monkeypatch.setattr(runner,'status',unavailable)
    with pytest.raises(PythonExecutionError,match='runtime unavailable'): await runner.execute({'code':'result=data'},[])

live=pytest.mark.skipif(os.getenv('PYFLOW_TEST_DOCKER')!='1',reason='Requires running Docker and Python image')

@live
async def test_live_python_output_and_stdlib():
    runner=PythonRunner()
    result=await runner.execute({'code':'import statistics\nprint("checked")\nresult={"mean":statistics.mean(data)}'},[2,4])
    assert result=={'data':{'mean':3},'stdout':'checked\n'}

@live
@pytest.mark.parametrize('code,message',[
    ('while True: pass','time limit'),
    ('result=1/0','ZeroDivisionError'),
    ('print("x")','Assign your output'),
    ('result=set([1])','not JSON serializable'),
    ('print("x"*100000)\nresult=[]','64 KiB'),
    ('open("/project-test","w").write("x")\nresult=[]','Read-only file system'),
    ('import socket\nsocket.create_connection(("1.1.1.1",443),timeout=1)\nresult=[]','Network is unreachable'),
])
async def test_live_limits(code,message):
    with pytest.raises(PythonExecutionError,match=message): await PythonRunner().execute({'code':code,'timeout':1},[])

@live
async def test_live_credentials_not_forwarded(monkeypatch):
    monkeypatch.setenv('PYFLOW_FAKE_SECRET','must-not-appear')
    result=await PythonRunner().execute({'code':'import os\nresult=os.environ.get("PYFLOW_FAKE_SECRET")'},[])
    assert result['data'] is None

@live
async def test_live_cancellation_cleanup():
    task=asyncio.create_task(PythonRunner().execute({'code':'while True: pass','timeout':30},[]))
    await asyncio.sleep(1);task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    process=await asyncio.create_subprocess_exec('docker','ps','--filter','name=pyflow-code-','--format','{{.Names}}',stdout=asyncio.subprocess.PIPE)
    stdout,_=await process.communicate()
    assert not stdout.strip()

async def test_python_api_reports_errors_without_losing_editor(tmp_path,monkeypatch):
    import httpx
    from backend.api.app import create_app
    async def unavailable(self): return {'ready':False,'setup':'Start Docker','image':'python:3.12-slim'}
    monkeypatch.setattr(PythonRunner,'status',unavailable)
    app=create_app(Repository(tmp_path),executor())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            assert not (await client.get('/python/status')).json()['ready']
            result=(await client.post('/python/test',json={'config':{'code':'result=data'},'input':[]})).json()
            assert result['success'] is False
            assert result['error']['category']=='PythonExecutionError'
            invalid=(await client.post('/python/test',json={'config':{'code':'','timeout':100}})).json()
            assert invalid['success'] is False

@live
async def test_live_data_libraries():
    code='''import pandas as pd
import numpy as np
import openpyxl
from io import BytesIO
frame = pd.DataFrame(data)
frame["name"] = frame["name"].str.strip()
book = openpyxl.Workbook()
book.active.append(["name"])
buffer = BytesIO()
book.save(buffer)
assert buffer.getbuffer().nbytes > 0
assert np.array([1,2]).sum() == 3
result = frame.to_dict(orient="records")
'''
    result=await PythonRunner().execute({'code':code,'timeout':10,'memory_mb':256},[{'name':' ada '},{'name':' sam '}])
    assert result['data']==[{'name':'ada'},{'name':'sam'}]
