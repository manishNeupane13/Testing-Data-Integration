import asyncio, logging, json, secrets as compare, time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
from backend.models.config import Connection, Endpoint, Workflow, Node, Schedule
from backend.storage.repository import Repository
from backend.security.safety import SecretStore, validate_config_secrets
from backend.http_engine.executor import HTTPExecutor
from backend.workflows.engine import WorkflowEngine, ConfiguredNode
from backend.json_processing.engine import JSONEngine, inspect_paths
from backend.core.settings import DATA_DIR, API_TOKEN
from backend.core.references import resolve
from backend.json_processing.workbench import DataWorkbench, Recipe
logger=logging.getLogger('pyflow')
class RunRequest(BaseModel):
    input: object = None
    idempotency_key: str | None = None

def create_app(repo=None,http=None):
    repo=repo or Repository(DATA_DIR);secret_store=SecretStore();http=http or HTTPExecutor(secret_store)
    engine=WorkflowEngine(repo,http);tasks={}
    @asynccontextmanager
    async def lifespan(app):
        repo.recover()
        scheduler=asyncio.create_task(schedule_loop())
        yield
        scheduler.cancel()
        await asyncio.gather(scheduler,return_exceptions=True)
        for task in list(tasks.values()): task.cancel()
        await asyncio.gather(*tasks.values(),return_exceptions=True)
        await http.close()
    async def authorize(authorization:str=Header(default='')):
        if API_TOKEN and not compare.compare_digest(authorization,'Bearer '+API_TOKEN): raise HTTPException(401,'Invalid API token')
    app=FastAPI(title='PyFlow API Studio',lifespan=lifespan,dependencies=[Depends(authorize)])
    app.state.repo=repo;app.state.http=http
    def get(kind,id):
        value=repo.get(kind,id)
        if value is None: raise HTTPException(404,'Not found')
        return value
    async def job_runner(id,run):
        job=get('jobs',id);repo.put('jobs',{**job,'status':'running'})
        try:
            result=http.secrets.redact(await run(id))
            if isinstance(result,dict): result={**result,'execution_id':id,'resource_id':job['resource_id']}
            repo.write_result(id,result)
            repo.put('results',{'id':id,'result':result})
            repo.put('jobs',{**job,'status':'completed','completed_at':datetime.now(timezone.utc).isoformat()})
            logger.info(json.dumps({'execution_id':id,'event':'job_completed'}))
        except asyncio.CancelledError:
            repo.put('jobs',{**job,'status':'cancelled'});raise
        except Exception as exc:
            error=http.secrets.redact({'category':type(exc).__name__,'message':str(exc)})
            repo.put('jobs',{**job,'status':'failed','error':error})
            logger.error(json.dumps({'execution_id':id,'event':'job_failed','error':error}))
    def submit(kind,resource,request,run):
        if request.idempotency_key:
            for job in repo.list('jobs'):
                if job.get('idempotency_key')==request.idempotency_key and job['kind']==kind and job['resource_id']==resource: return job
        id=str(uuid4());job=repo.put('jobs',{'id':id,'kind':kind,'resource_id':resource,'status':'queued','created_at':datetime.now(timezone.utc).isoformat(),'idempotency_key':request.idempotency_key})
        tasks[id]=asyncio.create_task(job_runner(id,run));tasks[id].add_done_callback(lambda _:tasks.pop(id,None))
        return job
    async def schedule_loop():
        while True:
            for schedule in repo.list('schedules'):
                now=time.time()
                if not schedule['enabled'] or schedule['next_run']>now: continue
                # Persist the next deadline before submission; no replay of missed ticks.
                repo.put('schedules',{**schedule,'next_run':now+schedule['interval_seconds']})
                row=repo.get('workflows',schedule['workflow_id'])
                if not row: continue
                if any(j['resource_id']==schedule['workflow_id'] and j['status'] in ('running','queued') for j in repo.list('jobs')): continue
                workflow=Workflow.model_validate(row)
                submit('workflow',workflow.id,RunRequest(idempotency_key=f"schedule:{schedule['id']}:{int(now)}"),lambda id,w=workflow:engine.execute(w,id))
            await asyncio.sleep(1)
    def register(kind,model):
        async def listing(): return repo.list(kind)
        async def read(id:str): return get(kind,id)
        async def save(body:dict):
            try:
                value=model.model_validate(body).model_dump();validate_config_secrets(value)
                return repo.put(kind,value)
            except Exception as exc: raise HTTPException(422,str(exc)) from exc
        async def delete(id:str): get(kind,id);repo.delete(kind,id);return {'deleted':id}
        app.add_api_route('/'+kind,listing,methods=['GET'],name='list_'+kind)
        app.add_api_route('/'+kind,save,methods=['POST'],name='save_'+kind)
        app.add_api_route('/'+kind+'/{id}',read,methods=['GET'],name='get_'+kind)
        app.add_api_route('/'+kind+'/{id}',delete,methods=['DELETE'],name='delete_'+kind)
    for kind,model in [('connections',Connection),('endpoints',Endpoint),('workflows',Workflow),('schedules',Schedule),('recipes',Recipe)]: register(kind,model)
    @app.get('/health')
    async def health(): return {'status':'ok'}
    @app.get('/{kind}/{id}/revisions')
    async def revisions(kind:str,id:str):
        if kind not in ('connections','endpoints','workflows'): raise HTTPException(404,'Not found')
        get(kind,id)
        with repo.lock: rows=repo.db.execute('SELECT value FROM revisions WHERE kind=? AND id=? ORDER BY version',(kind,id)).fetchall()
        return [json.loads(row[0]) for row in rows]
    @app.get('/settings')
    async def settings(): return {'data_directory':str(repo.directory),'metrics':http.metrics,'authentication_enabled':bool(API_TOKEN)}
    @app.post('/connections/{id}/test')
    async def test_connection(id:str):
        c=Connection.model_validate(get('connections',id))
        try: return await http.execute(c,Endpoint(name='Connection test',connection_id=id,path=c.test_path))
        except Exception as exc: raise HTTPException(400,http.secrets.redact(str(exc))) from exc
    @app.post('/endpoints/{id}/duplicate')
    async def duplicate(id:str):
        value=get('endpoints',id);value.update(id=str(uuid4()),name=value['name']+' copy');return repo.put('endpoints',value)
    @app.post('/endpoints/{id}/execute',status_code=202)
    @app.post('/endpoints/{id}/test',status_code=202)
    async def execute_endpoint(id:str,request:RunRequest=RunRequest()):
        e=Endpoint.model_validate(resolve(get('endpoints',id),{'input':request.input}));c=Connection.model_validate(get('connections',e.connection_id))
        return submit('endpoint',id,request,lambda _:http.execute(c,e))
    @app.post('/workflows/{id}/execute',status_code=202)
    @app.post('/webhooks/{id}',status_code=202)
    async def execute_workflow(id:str,request:RunRequest=RunRequest()):
        w=Workflow.model_validate(get('workflows',id))
        return submit('workflow',id,request,lambda job_id:engine.execute(w,job_id,request.input))
    @app.post('/python/test')
    async def python_test(body:dict):
        try:
            output=await engine.python.execute(body['config'],body.get('input'))
            return {'success':True,'output':http.secrets.redact(output)}
        except Exception as exc:
            return {'success':False,'error':http.secrets.redact({'category':type(exc).__name__,'message':str(exc)})}
    @app.get('/python/status')
    async def python_status(): return await engine.python.status()
    @app.post('/nodes/test')
    async def test_node(body:dict):
        node=Node.model_validate(body['node']);context=body.get('context',{'nodes':{},'input':None});context['execution_id']=str(uuid4())
        try: return http.secrets.redact(await ConfiguredNode(node,engine).execute(context))
        except Exception as exc: raise HTTPException(400,http.secrets.redact(str(exc))) from exc
    @app.get('/jobs')
    async def jobs(): return repo.list('jobs')
    @app.get('/jobs/{id}')
    async def job(id:str): return get('jobs',id)
    @app.post('/jobs/{id}/cancel')
    async def cancel(id:str):
        job=get('jobs',id)
        if id in tasks:
            tasks[id].cancel()
            repo.put('jobs',{**job,'status':'cancelled'})
        return get('jobs',id)
    @app.get('/jobs/{id}/nodes/{node_id}')
    async def node_output(id:str,node_id:str): return get('node_outputs',id+'_'+node_id)
    @app.get('/results')
    async def results(): return [{'id':r['id']} for r in repo.list('results')]
    @app.get('/results/{id}')
    async def result(id:str): return get('results',id)
    @app.get('/results/{id}/download')
    async def download(id:str):
        get('results',id);return FileResponse(repo.directory/(id+'.jsonl'),filename=id+'.jsonl')
    @app.get('/artifacts/{id}')
    async def artifact(id:str): return FileResponse(get('artifacts',id)['path'])
    @app.post('/data/import')
    async def import_data(body:dict):
        try:
            rows=DataWorkbench().import_text(body['text'],body['format'])
            return {'data':http.secrets.redact(rows),'count':len(rows)}
        except (ValueError,KeyError) as exc: raise HTTPException(422,str(exc)) from exc
    @app.post('/data/process')
    async def process_data(body:dict):
        try:
            recipe=Recipe.model_validate(body['recipe'])
            report=DataWorkbench().run(body['data'],recipe,body.get('search',''),body.get('sort_field'),body.get('descending',False))
            report=http.secrets.redact(report)
            if body.get('save',False):
                id=str(uuid4());repo.write_result(id,report);repo.put('results',{'id':id,'result':report});report['result_id']=id
            return report
        except Exception as exc: raise HTTPException(422,http.secrets.redact(str(exc))) from exc
    @app.post('/json/transform')
    async def transform(body:dict):
        try: return {'data':JSONEngine().apply(body['data'],body['config'])}
        except Exception as exc: raise HTTPException(422,str(exc)) from exc
    @app.post('/json/inspect')
    async def inspect(body:dict):
        search=body.get('search','').lower()
        return [p for p in inspect_paths(body['data']) if search in p['path'].lower() or search in p['preview'].lower()]
    return app
app=create_app()
