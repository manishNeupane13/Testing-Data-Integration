import asyncio, json
import httpx, pytest
from pydantic import ValidationError
from backend.models.config import *
from backend.http_engine.executor import HTTPExecutor
from backend.security.safety import SecretStore,validate_url,SecurityError,validate_config_secrets
from backend.json_processing.engine import JSONEngine
from backend.core.references import resolve
from backend.storage.repository import Repository
from backend.workflows.engine import WorkflowEngine
from backend.api.app import create_app
from examples.mock_api import handler

async def allow_mock(url,hosts):
    if httpx.URL(url).host!='mock.example': raise SecurityError('Unexpected host')
def executor(handler_fn=handler): return HTTPExecutor(SecretStore(),httpx.AsyncClient(transport=httpx.MockTransport(handler_fn)),allow_mock)
def connection(**kwargs): return Connection(id='mock',name='mock',base_url='https://mock.example',approved_hosts=['mock.example'],requests_per_second=10000,**kwargs)
def endpoint(**kwargs): return Endpoint(name='test',connection_id='mock',path='/drivers',**kwargs)

@pytest.mark.parametrize('kind,credentials,header,query',[
 ('none',{},None,None),('bearer',{'token':'hidden'},'Bearer hidden',None),
 ('basic',{'username':'u','password':'p'},'Basic dTpw',None),
 ('api_key_header',{'api_key':'hidden'},'hidden',None),('api_key_query',{'api_key':'hidden'},None,'hidden')])
async def test_auth(monkeypatch,kind,credentials,header,query):
    monkeypatch.setenv('TEST_SECRET',json.dumps(credentials))
    def handle(r):
        if header: assert r.headers['X-API-Key' if kind=='api_key_header' else 'Authorization']==header
        if query: assert r.url.params['X-API-Key']==query
        return httpx.Response(200,json={'ok':True})
    x=executor(handle)
    try: assert (await x.execute(connection(auth_type=kind,credential_ref='TEST_SECRET'),endpoint()))['data']=={'ok':True}
    finally: await x.close()

async def test_request_construction():
    def handle(r):
        assert r.url.path=='/drivers/a%2Fb' or r.url.raw_path.startswith(b'/drivers/a%2Fb')
        assert r.url.params['q']=='hello';assert json.loads(r.content)=={'value':3}
        return httpx.Response(200,json={'items':[1]})
    x=executor(handle);e=Endpoint(name='test',connection_id='mock',path='/drivers/{id}',path_params={'id':'a/b'},method='POST',body={'value':3},query={'q':'hello'},output_path='items')
    assert (await x.execute(connection(),e))['data']==[1];await x.close()

async def test_pagination_and_loop():
    x=executor();r=await x.execute(connection(),endpoint(pagination=Pagination(strategy='offset',parameter='offset',items_path='items',page_size=2)))
    assert len(r['data'])==3;assert r['pagination']['pages']==3;await x.close()
    x=executor(lambda r:httpx.Response(200,json={'items':[1],'next':'same'}))
    r=await x.execute(connection(),endpoint(pagination=Pagination(strategy='cursor',items_path='items')))
    assert r['pagination']['partial']['category']=='pagination_loop';await x.close()

async def test_partial_failure():
    count=0
    def handle(r):
        nonlocal count;count+=1
        return httpx.Response(200,json={'items':[1]}) if count==1 else httpx.Response(500)
    x=executor(handle);r=await x.execute(connection(),endpoint(pagination=Pagination(strategy='page',parameter='page',items_path='items')))
    assert r['data']==[1] and r['pagination']['partial'];await x.close()

@pytest.mark.parametrize('method,unsafe,expected',[('GET',False,3),('POST',False,1),('PATCH',True,3)])
async def test_retry(method,unsafe,expected):
    x=executor(lambda r:httpx.Response(503,headers={'Retry-After':'0'}))
    with pytest.raises(httpx.HTTPStatusError): await x.execute(connection(),endpoint(method=method,retry=RetryPolicy(backoff=0,retry_unsafe=unsafe)))
    assert x.metrics['requests']==expected;await x.close()

async def test_redirect_and_size(monkeypatch):
    x=executor(lambda r:httpx.Response(302,headers={'Location':'http://127.0.0.1'}))
    with pytest.raises(ValueError,match='Redirect'): await x.execute(connection(),endpoint())
    await x.close();monkeypatch.setattr('backend.http_engine.executor.MAX_RESPONSE_BYTES',10)
    x=executor(lambda r:httpx.Response(200,content=b'x'*11))
    with pytest.raises(ValueError,match='size'): await x.execute(connection(),endpoint())
    await x.close()

@pytest.mark.parametrize('url,hosts',[('http://127.0.0.1', ['127.0.0.1']),('http://[::1]',['::1']),('file:///etc/passwd',[]),('https://user:pass@example.com',['example.com']),('https://example.com',[])])
async def test_ssrf(url,hosts):
    with pytest.raises(SecurityError): await validate_url(url,hosts)

def test_references_and_json():
    ctx={'nodes':{'fetch':{'output':{'data':[{'id':7}]}}}}
    assert resolve('{{ nodes.fetch.output.data[0].id }}',ctx)==7
    assert resolve('/x/{{ nodes.fetch.output.data[0].id }}',ctx)=='/x/7'
    data=[{'id':1,'active':True,'nested':{'v':3}},{'id':2,'active':False,'nested':{'v':4}}];e=JSONEngine()
    assert len(e.apply(data,{'operation':'filter','expression':'[?active == `true`]'}))==1
    assert e.apply(data,{'operation':'select','fields':{'value':'nested.v'}})==[{'value':3},{'value':4}]
    assert e.apply(data,{'operation':'flatten'})[0]['nested.v']==3
    assert e.apply(data,{'operation':'aggregate','expression':'active','method':'sum','field':'nested.v'})=={'True':3,'False':4}
    assert len(e.apply(data+data,{'operation':'deduplicate','expression':'id'}))==2
    assert len(e.apply(data,{'operation':'batch','size':1}))==2
    assert e.apply([[{'id':1}],[{'id':1,'name':'a'}]],{'operation':'join','left_key':'id','right_key':'id'})==[{'id':1,'name':'a'}]

@pytest.mark.parametrize('nodes',[[{'id':'a','type':'manual','dependencies':['a']}],[{'id':'a','type':'manual','dependencies':['missing']}]])
def test_graph_validation(nodes):
    with pytest.raises(ValidationError): Workflow(name='invalid',nodes=nodes)

async def test_example_workflows(tmp_path):
    from pathlib import Path
    spec=json.loads(Path('examples/blueprints.json').read_text());repo=Repository(tmp_path)
    for kind,rows in spec.items():
        for row in rows: repo.put(kind,row)
    x=executor();engine=WorkflowEngine(repo,x)
    results={row['id']:await engine.execute(Workflow.model_validate(row),row['id']) for row in spec['workflows']}
    assert len(results['example_a']['nodes']['active']['output']['data'])==2
    assert len(results['example_b']['nodes']['details']['output']['data'])==3
    assert len(results['example_d']['nodes']['merge']['output']['data'])==4
    assert list(tmp_path.glob('*.parquet'));await x.close()

async def test_parallel_nodes(tmp_path):
    active=0;peak=0
    async def handle(r):
        nonlocal active,peak
        active+=1;peak=max(peak,active);await asyncio.sleep(.03);active-=1
        return httpx.Response(200,json=[])
    repo=Repository(tmp_path);repo.put('connections',connection().model_dump());e=endpoint();repo.put('endpoints',e.model_dump());x=executor(handle)
    w=Workflow(name='parallel',nodes=[Node(id=id,type='http',config={'endpoint_id':e.id}) for id in ('a','b')])
    await WorkflowEngine(repo,x).execute(w,'run');assert peak==2;await x.close()

def test_persistence_redaction_recovery(tmp_path,monkeypatch):
    repo=Repository(tmp_path);repo.put('jobs',{'id':'job','status':'running'});Repository(tmp_path).recover()
    assert repo.get('jobs','job')['status']=='interrupted'
    repo.put('endpoints',{'id':'e'});repo.put('endpoints',{'id':'e'});assert repo.get('endpoints','e')['version']==2
    monkeypatch.setenv('SECRET','{"token":"super-secret"}')
    secrets=SecretStore();secrets.get('SECRET')
    assert secrets.redact({'message':'got super-secret','Authorization':'Bearer something'})=={'message':'got [REDACTED]','Authorization':'[REDACTED]'}
    with pytest.raises(SecurityError): validate_config_secrets({'headers':{'Authorization':'secret'}})

async def test_api_jobs(tmp_path):
    repo=Repository(tmp_path);x=executor();app=create_app(repo,x)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            assert (await client.post('/connections',json=connection().model_dump())).status_code==200
            e=endpoint();await client.post('/endpoints',json=e.model_dump())
            response=await client.post(f'/endpoints/{e.id}/execute',json={'idempotency_key':'once'});assert response.status_code==202
            job=response.json()
            again=(await client.post(f'/endpoints/{e.id}/execute',json={'idempotency_key':'once'})).json();assert again['id']==job['id']
            for _ in range(100):
                status=(await client.get('/jobs/'+job['id'])).json()
                if status['status'] in ('completed','failed'): break
                await asyncio.sleep(.01)
            assert status['status']=='completed'
            assert (await client.get('/results/'+job['id'])).status_code==200
            assert (await client.get('/results/'+job['id']+'/download')).status_code==200

@pytest.mark.parametrize('kind',['oauth2_client_credentials','oauth2_authorization_code'])
async def test_oauth_cache_and_rotation(monkeypatch,kind):
    from urllib.parse import parse_qs
    from backend.authentication.provider import AuthenticationProvider
    monkeypatch.setenv('OAUTH',json.dumps({'client_id':'id','client_secret':'secret-value','token_url':'https://mock.example/token','refresh_token':'initial'}))
    monkeypatch.setattr('backend.authentication.provider.validate_url',allow_mock)
    calls=[]
    def handle(request):
        fields=parse_qs(request.content.decode());calls.append(fields)
        if kind=='oauth2_authorization_code': assert fields['refresh_token']==['initial' if len(calls)==1 else 'rotated']
        return httpx.Response(200,json={'access_token':'access-secret','refresh_token':'rotated','expires_in':1000})
    store=SecretStore();auth=AuthenticationProvider(store);c=connection(auth_type=kind,credential_ref='OAUTH')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        h={};await auth.apply(c,client,h,{})
        assert h['Authorization']=='Bearer access-secret'
        await auth.apply(c,client,{},{});assert len(calls)==1
        auth.cache.clear();await auth.apply(c,client,{},{});assert len(calls)==2
        assert store.redact('access-secret rotated')=='[REDACTED] [REDACTED]'

async def test_dns_pinning(monkeypatch):
    from backend.security.safety import pinned_target
    target,headers,extensions=pinned_target('https://api.example:8443/path','93.184.216.34',{})
    assert target.host=='93.184.216.34'
    assert headers['Host']=='api.example:8443';assert extensions['sni_hostname']=='api.example'
    async def resolve(*args,**kwargs): return [(2,1,6,'',('93.184.216.34',443))]
    monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',resolve)
    assert await validate_url('https://api.example',['api.example'])=='93.184.216.34'

@pytest.mark.parametrize('strategy',['page','cursor','next_url','link'])
async def test_other_pagination(strategy):
    count=0
    def handle(r):
        nonlocal count;count+=1
        if count==1:
            return httpx.Response(200,json={'items':[1],'next':'https://mock.example/second' if strategy=='next_url' else 'second'},headers={'Link':'<https://mock.example/second>; rel="next"'})
        return httpx.Response(200,json={'items':[]})
    x=executor(handle)
    result=await x.execute(connection(),endpoint(pagination=Pagination(strategy=strategy,items_path='items')))
    assert result['data']==[1];assert result['pagination']['pages']==2;await x.close()

async def test_scheduler_and_cancel(tmp_path):
    repo=Repository(tmp_path);x=executor();app=create_app(repo,x)
    w=Workflow(id='scheduled',name='scheduled',nodes=[Node(id='start',type='manual')]);repo.put('workflows',w.model_dump())
    repo.put('schedules',Schedule(workflow_id=w.id,interval_seconds=10).model_dump())
    async with app.router.lifespan_context(app):
        for _ in range(100):
            if any(j['status']=='completed' for j in repo.list('jobs')): break
            await asyncio.sleep(.01)
        assert repo.list('jobs')[0]['status']=='completed'
        assert repo.list('schedules')[0]['next_run']>0

async def test_job_cancellation(tmp_path):
    started=asyncio.Event()
    async def slow(request): started.set();await asyncio.sleep(30);return httpx.Response(200,json=[])
    repo=Repository(tmp_path);repo.put('connections',connection().model_dump());e=endpoint();repo.put('endpoints',e.model_dump());x=executor(slow);app=create_app(repo,x)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            job=(await client.post(f'/endpoints/{e.id}/execute',json={})).json();await started.wait()
            await client.post(f"/jobs/{job['id']}/cancel");await asyncio.sleep(.01)
            assert repo.get('jobs',job['id'])['status']=='cancelled'

async def test_next_url_preserves_query():
    def handle(request):
        if request.url.path=='/drivers': return httpx.Response(200,json={'items':[1],'next':'https://mock.example/next?cursor=second'})
        assert request.url.params['cursor']=='second'
        return httpx.Response(200,json={'items':[2],'next':None})
    x=executor(handle)
    result=await x.execute(connection(),endpoint(pagination=Pagination(strategy='next_url',items_path='items')))
    assert result['data']==[1,2];await x.close()

async def test_samsara_cursor_empty_page_and_terminal_cursor():
    calls=[]
    def handle(request):
        cursor=request.url.params.get('after');calls.append(cursor)
        assert request.url.params['limit']=='100'
        assert request.url.params['tagIds']=='tag-one'
        pages={None:{'data':[{'id':'1'}],'pagination':{'hasNextPage':True,'endCursor':'a'}},
               'a':{'data':[],'pagination':{'hasNextPage':True,'endCursor':'b'}},
               'b':{'data':[{'id':'2'}],'pagination':{'hasNextPage':False,'endCursor':'c'}}}
        return httpx.Response(200,json=pages[cursor])
    x=executor(handle)
    e=endpoint(query={'tagIds':'tag-one'},pagination=Pagination(strategy='cursor',items_path='data',next_path='pagination.endCursor',parameter='after',page_size=100,stop_expression='pagination.hasNextPage == `false`'))
    result=await x.execute(connection(),e)
    assert result['data']==[{'id':'1'},{'id':'2'}]
    assert calls==[None,'a','b']
    assert result['pagination']['partial'] is None
    await x.close()
