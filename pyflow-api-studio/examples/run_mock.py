import asyncio,json,tempfile
from pathlib import Path
import httpx
from backend.storage.repository import Repository
from backend.security.safety import SecretStore
from backend.http_engine.executor import HTTPExecutor
from backend.workflows.engine import WorkflowEngine
from backend.models.config import Workflow
from examples.mock_api import handler
async def main():
    spec=json.loads(Path('examples/blueprints.json').read_text());repo=Repository('data/mock')
    for kind,rows in spec.items():
        for row in rows: repo.put(kind,row)
    async def mock_only(url,hosts):
        if httpx.URL(url).host!='mock.example': raise ValueError('Unexpected mock host')
    executor=HTTPExecutor(SecretStore(),httpx.AsyncClient(transport=httpx.MockTransport(handler)),mock_only)
    try:
        for row in spec['workflows']:
            output=await WorkflowEngine(repo,executor).execute(Workflow.model_validate(row),row['id'])
            print(row['name'],json.dumps(output,default=str)[:500])
    finally: await executor.close()
if __name__=='__main__': asyncio.run(main())
