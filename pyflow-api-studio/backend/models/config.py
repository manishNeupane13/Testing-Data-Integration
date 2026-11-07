from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator
from uuid import uuid4

def uid(): return str(uuid4())
class ConfigModel(BaseModel):
    model_config = {"extra": "forbid"}
    id: str = Field(default_factory=uid, pattern=r"^[A-Za-z0-9_-]+$")
    version: int = 1
class Connection(ConfigModel):
    name: str
    base_url: str
    auth_type: Literal['none','api_key_header','api_key_query','bearer','basic','oauth2_client_credentials','oauth2_authorization_code'] = 'none'
    credential_ref: str | None = None
    auth_name: str = 'X-API-Key'
    default_headers: dict[str,str] = Field(default_factory=dict)
    timeout: float = Field(30, gt=0, le=300)
    requests_per_second: float = Field(5, gt=0)
    approved_hosts: list[str] = Field(default_factory=list)
    enabled: bool = True
    test_path: str = '/'
class RetryPolicy(BaseModel):
    attempts: int = Field(3, ge=1, le=10)
    backoff: float = Field(.2, ge=0, le=60)
    retry_unsafe: bool = False
class Pagination(BaseModel):
    strategy: Literal['none','cursor','offset','page','link','next_url'] = 'none'
    items_path: str | None = None
    next_path: str = 'next'
    parameter: str = 'cursor'
    size_parameter: str = 'limit'
    page_size: int = Field(100, gt=0)
    max_pages: int = Field(100, gt=0, le=10000)
    max_records: int = Field(100000, gt=0)
    start: int = 1
    stop_expression: str | None = None
class Endpoint(ConfigModel):
    name: str
    connection_id: str
    method: Literal['GET','POST','PUT','PATCH','DELETE'] = 'GET'
    path: str
    path_params: dict[str,Any] = Field(default_factory=dict)
    query: dict[str,Any] = Field(default_factory=dict)
    headers: dict[str,Any] = Field(default_factory=dict)
    body: Any = None
    content_type: str = 'application/json'
    response_format: Literal['json','text'] = 'json'
    pagination: Pagination = Field(default_factory=Pagination)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout: float | None = Field(None, gt=0, le=300)
    output_path: str | None = None
    capture_raw: bool = False
class Node(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    type: Literal['python','manual','webhook','http','filter','transform','merge','conditional','batch','save_json','export_csv','export_parquet']
    dependencies: list[str] = Field(default_factory=list)
    config: dict[str,Any] = Field(default_factory=dict)
    retry: RetryPolicy = Field(default_factory=lambda: RetryPolicy(attempts=1))
    @model_validator(mode='after')
    def validate_python(self):
        if self.type=='python':
            from backend.python_runtime.runner import PythonConfig
            PythonConfig.model_validate(self.config)
        return self

class Workflow(ConfigModel):
    name: str
    nodes: list[Node]
    @model_validator(mode='after')
    def validate_graph(self):
        ids={n.id for n in self.nodes}
        if len(ids)!=len(self.nodes): raise ValueError('Duplicate node IDs')
        done=set()
        while len(done)<len(ids):
            ready={n.id for n in self.nodes if n.id not in done and set(n.dependencies)<=done}
            if not ready: raise ValueError('Cycle or missing dependency')
            done |= ready
        return self

class Schedule(ConfigModel):
    workflow_id: str
    interval_seconds: int = Field(3600, ge=10)
    enabled: bool = True
    next_run: float = 0
