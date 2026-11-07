import asyncio, json, csv, io
from abc import ABC, abstractmethod
from typing import TypedDict, Any
class NodeOutput(TypedDict):
    data: Any
class ExecutionContext(TypedDict):
    nodes: dict[str,dict[str,NodeOutput]]
    execution_id: str
    input: Any
from pathlib import Path
import jmespath
from backend.models.config import Endpoint, Connection
from backend.core.references import resolve
from backend.json_processing.engine import JSONEngine
from backend.core.settings import CONCURRENCY
from backend.python_runtime.runner import PythonRunner
class BaseNode(ABC):
    @abstractmethod
    async def execute(self,context:ExecutionContext) -> NodeOutput: ...
class ConfiguredNode(BaseNode):
    def __init__(self,node,engine): self.node=node;self.engine=engine
    async def execute(self,context):
        n=self.node;e=self.engine
        c=resolve({k:v for k,v in n.config.items() if n.type!='python' or k!='code'},context)
        if n.type=='python': c['code']=n.config['code']
        incoming=[context['nodes'][d]['output']['data'] for d in n.dependencies]
        data=c.get('input',incoming[0] if incoming else context.get('input'))
        if n.type=='python': return await e.python.execute(c,data)
        if n.type in ('manual','webhook'): return {'data':data}
        if n.type=='http':
            endpoint=Endpoint.model_validate(e.repo.get('endpoints',c['endpoint_id']))
            connection=Connection.model_validate(e.repo.get('connections',endpoint.connection_id))
            async def call(item):
                async with e.fanout:
                    config=resolve(endpoint.model_dump(),{**context,'item':item})
                    config.update(resolve(c.get('overrides',{}),{**context,'item':item}))
                    return await e.http.execute(connection,Endpoint.model_validate(config))
            if 'foreach' in c:
                rows=c['foreach']
                if not isinstance(rows,list): raise ValueError('foreach must resolve to an array')
                result=[]
                for start in range(0,len(rows),CONCURRENCY): result.extend(await asyncio.gather(*(call(item) for item in rows[start:start+CONCURRENCY])))
                return {'data':[r['data'] for r in result]}
            return await call(None)
        if n.type=='conditional':
            matched=bool(jmespath.search(c['expression'],data))
            return {'data':data if matched else None,'matched':matched}
        if n.type in ('filter','transform','merge','batch'):
            if n.type=='merge': data=c.get('input',incoming);c.setdefault('operation','merge')
            if n.type=='batch': c['operation']='batch'
            return {'data':e.json.apply(data,c)}
        if n.type.startswith('export_') or n.type=='save_json':
            identifier=context['execution_id']+'_'+n.id
            if n.type=='save_json': path=e.repo.write_result(identifier,data)
            elif n.type=='export_csv':
                path=str(e.repo.directory/(identifier+'.csv'))
                keys=list(dict.fromkeys(k for row in data for k in row))
                with open(path,'w',newline='') as f:
                    writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader();writer.writerows(data)
            else:
                import pyarrow as pa, pyarrow.parquet as pq
                path=str(e.repo.directory/(identifier+'.parquet'));pq.write_table(pa.Table.from_pylist(data),path)
            e.repo.put('artifacts',{'id':identifier,'path':path})
            return {'data':data,'artifact_id':identifier}
        raise ValueError('Unsupported node')
class WorkflowEngine:
    def __init__(self,repo,http): self.repo=repo;self.http=http;self.python=PythonRunner();self.json=JSONEngine();self.fanout=asyncio.Semaphore(CONCURRENCY)
    async def execute(self,workflow,execution_id,input=None):
        context={'nodes':{},'execution_id':execution_id,'input':input};pending={n.id:n for n in workflow.nodes}
        while pending:
            ready=[n for n in pending.values() if all(d in context['nodes'] for d in n.dependencies)]
            if not ready: raise ValueError('Invalid graph')
            async def run(node):
                for attempt in range(node.retry.attempts):
                    try:
                        result=await ConfiguredNode(node,self).execute(context)
                        safe=self.http.secrets.redact(result)
                        self.repo.put('node_outputs',{'id':execution_id+'_'+node.id,'output':safe})
                        return node.id,{'output':safe}
                    except Exception:
                        # HTTP retries are governed solely by the endpoint policy.
                        if node.type=='http' or attempt+1==node.retry.attempts: raise
                        await asyncio.sleep(node.retry.backoff*2**attempt)
            tasks=[asyncio.create_task(run(n)) for n in ready]
            try: results=await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks: task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
                raise
            context['nodes'].update(dict(results))
            for n in ready: del pending[n.id]
        return context
