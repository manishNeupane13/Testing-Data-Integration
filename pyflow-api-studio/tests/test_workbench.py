import pytest
import httpx
from backend.json_processing.workbench import DataWorkbench,Recipe
from backend.api.app import create_app
from backend.storage.repository import Repository
from test_platform import executor


def test_import_and_full_dataset_reporting():
    work=DataWorkbench()
    rows=work.import_text('id,name,age\n 1 , Ada ,20\n2,,bad\n2,Lin,-1\n','csv')
    recipe=Recipe(id='checks',name='Checks',steps=[{'operation':'trim','field':'id'},{'operation':'trim','field':'name'},{'operation':'cast','field':'age','type':'integer'}],rules=[{'check':'unique','field':'id'},{'check':'required','field':'name'},{'check':'min','field':'age','value':0}])
    report=work.run(rows,recipe,search='Ada',sort_field='age')
    assert report['summary']['total']==3
    assert report['summary']['passed']==1
    assert report['summary']['failed']==2
    assert report['summary']['visible']==1
    assert report['data'][0]=={'id':'1','name':'Ada','age':20}
    assert rows[0]['name']==' Ada ' # source unchanged
    assert {issue['rule'] for issue in report['issues']}=={'cleaning:cast','unique','required','min'}


def test_conditions_and_mixed_sort():
    rows=[{'id':'a','active':True,'distance':-1},{'id':'b','active':False,'distance':None},{'id':'c','active':True,'distance':2}]
    recipe=Recipe(id='if',name='if',rules=[{'check':'condition','when':'active == `true`','expression':'distance >= `0`','message':'Active driver needs nonnegative distance'}])
    report=DataWorkbench().run(rows,recipe,sort_field='distance')
    assert report['summary']['failed']==1
    assert report['issues'][0]['reason']=='Active driver needs nonnegative distance'
    assert [r['id'] for r in report['view']]==['a','c','b']


@pytest.mark.parametrize('value',['1.5','NaN','Infinity'])
def test_invalid_integer(value):
    with pytest.raises(ValueError): DataWorkbench().cast(value,'integer')


def test_import_formats_and_invalid_shape():
    w=DataWorkbench()
    assert w.import_text('{"a":1}\n{"a":2}','jsonl')==[{'a':1},{'a':2}]
    assert w.import_text('[{"a":1}]','json')==[{'a':1}]
    with pytest.raises(ValueError): w.import_text('{"items":[]}','json')
    with pytest.raises(ValueError): w.import_text('[]','unsupported')


async def test_recipe_and_report_api(tmp_path):
    app=create_app(Repository(tmp_path),executor())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            recipe={'id':'checks','name':'Checks','rules':[{'check':'required','field':'id'}]}
            assert (await client.post('/recipes',json=recipe)).status_code==200
            assert (await client.get('/recipes')).json()[0]['name']=='Checks'
            response=await client.post('/data/process',json={'data':[{'id':'1'},{'id':''}],'recipe':recipe,'save':True})
            report=response.json();assert report['summary']['failed']==1
            stored=(await client.get('/results/'+report['result_id'])).json()
            assert stored['result']['summary']['total']==2
            invalid=await client.post('/data/process',json={'data':[],'recipe':{**recipe,'rules':[{'check':'unknown'}]}})
            assert invalid.status_code==422

async def test_recipe_in_workflow(tmp_path):
    from backend.models.config import Workflow
    from backend.workflows.engine import WorkflowEngine
    repo=Repository(tmp_path);http=executor()
    workflow=Workflow.model_validate({'name':'Check imported rows','nodes':[
        {'id':'source','type':'manual'},
        {'id':'check','type':'transform','dependencies':['source'],'config':{'operation':'validate_records','recipe':{'id':'recipe','name':'Required ID','rules':[{'check':'required','field':'id'}]}}},
        {'id':'passed','type':'transform','dependencies':['check'],'config':{'operation':'extract','expression':'passed'}}]})
    result=await WorkflowEngine(repo,http).execute(workflow,'test',[{'id':'one'},{'id':''}])
    assert result['nodes']['passed']['output']['data']==[{'id':'one'}]
    await http.close()
