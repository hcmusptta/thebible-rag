"""Measure a minimal real provider call and system memory; never send corpus text."""
import argparse
import ctypes
import json
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services.model import LLMService, LLMRequestError, LLMOutputError
from services.llm_usage import measure_llm_usage


def system_memory():
    if os.name=='nt':
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(name,ctypes.c_ulonglong) for name in
                ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
        state=Memory(); state.length=ctypes.sizeof(state)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)): return {}
        return {'total_bytes':state.total,'used_bytes':state.total-state.available}
    try:
        return {'total_bytes':os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')}
    except (ValueError,OSError,AttributeError): return {}


def gpu_status():
    try:
        result=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total,memory.used','--format=csv,noheader,nounits'],
                              capture_output=True,text=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode: return []
        return [{'name':name.strip(),'total_mib':int(total),'used_mib':int(used)}
                for name,total,used in (line.rsplit(',',2) for line in result.stdout.splitlines())]
    except (FileNotFoundError,subprocess.TimeoutExpired,ValueError): return []


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider',required=True,choices=['ollama','google'])
    parser.add_argument('--reasoning',choices=['true','false'],default='false')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    os.environ['OLLAMA_REASONING']=args.reasoning
    report={'provider':args.provider,'python':platform.python_version(),'platform':platform.platform(),
            'cpu':platform.processor(),'ram':system_memory(),'gpu_before':gpu_status(),
            'reasoning':args.reasoning,'measurement_scope':'System-wide memory includes other processes; one synthetic JSON request.'}
    stop=threading.Event(); samples=[]
    def sample():
        while not stop.is_set():
            samples.append({'ram':system_memory(),'gpu':gpu_status()})
            stop.wait(1)
    monitor=threading.Thread(target=sample,daemon=True); monitor.start()
    started=time.monotonic()
    usage={}
    try:
        service=LLMService(provider_order=args.provider)
        with measure_llm_usage() as usage:
            answer=service._generate_with_fallback('Return the JSON object with ok=true.',
                response_schema={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False})
            if json.loads(answer).get('ok') is not True:
                raise LLMOutputError('Provider did not return the requested JSON value.')
        report.update(status='passed',answer=json.loads(answer),llm_usage=usage)
    except (LLMRequestError,LLMOutputError,ValueError) as error:
        report.update(status='failed',error_type=type(error).__name__,message=str(error),llm_usage=usage)
    finally:
        stop.set(); monitor.join(timeout=6)
        report['seconds']=time.monotonic()-started
        report['system_ram_used_bytes_peak']=max((s['ram'].get('used_bytes',0) for s in samples),default=0)
        report['system_gpu_used_mib_peak']=max((sum(g['used_mib'] for g in s['gpu']) for s in samples),default=0)
    if args.provider=='ollama':
        try:
            base=os.getenv('OLLAMA_BASE_URL','http://localhost:11434').rstrip('/')
            with urllib.request.urlopen(base+'/api/tags',timeout=5) as response:
                report['model_artifact']=[{k:m.get(k) for k in ('name','digest','size','details')}
                    for m in json.load(response).get('models',[]) if m['name']==os.getenv('OLLAMA_MODEL','qwen3:8b')]
        except (OSError,ValueError): report['model_artifact']=None
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:report.get(key) for key in ('provider','status','seconds','llm_usage','error_type')},ensure_ascii=False))
    return 0 if report.get('status')=='passed' else 1


if __name__=='__main__': raise SystemExit(main())
