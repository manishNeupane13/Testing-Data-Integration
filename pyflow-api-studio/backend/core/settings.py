import os
from pathlib import Path
DATA_DIR = Path(os.getenv('PYFLOW_DATA_DIR','data')).resolve()
CONCURRENCY = int(os.getenv('PYFLOW_CONCURRENCY','8'))
MAX_RESPONSE_BYTES = int(os.getenv('PYFLOW_MAX_RESPONSE_BYTES','10485760'))
API_TOKEN = os.getenv('PYFLOW_API_TOKEN','')
