#!/usr/bin/env python3
from __future__ import annotations
import json, os, shlex, subprocess, time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

HOST=os.getenv('GENOA_HOST','0.0.0.0'); PORT=int(os.getenv('GENOA_PORT','8080'))
SPLAT_BIN=os.getenv('SPLAT_BIN','./splat'); WORKDIR=Path(os.getenv('SPLAT_WORKDIR','.')).resolve()
DASHBOARD=Path(__file__).with_name('dashboard.html')
STATS={'total_runs':0,'success_runs':0,'failed_runs':0,'avg_runtime_seconds':0.0,'last_run_at':None,'last_returncode':None}
LOCK=Lock()

class GenoaHandler(BaseHTTPRequestHandler):
    server_version='genoa-sidecar/1.1'
    def do_GET(self):
        if self.path=='/' or self.path.startswith('/dashboard'):
            return self._serve_dashboard()
        if self.path=='/healthz':
            return self._send_json(HTTPStatus.OK, {'status':'ok'})
        if self.path=='/api/v1/stats':
            with LOCK: total=STATS['total_runs']; succ=STATS['success_runs']; avg=STATS['avg_runtime_seconds']; last=STATS['last_run_at']; rc=STATS['last_returncode']
            return self._send_json(HTTPStatus.OK, {'total_runs':total,'success_runs':succ,'failed_runs':STATS['failed_runs'],'success_rate':(succ/total if total else 0.0),'avg_runtime_seconds':avg,'last_run_at':last,'last_returncode':rc})
        self._send_json(HTTPStatus.NOT_FOUND, {'error':'not found'})
    def do_POST(self):
        if self.path!='/api/v1/splat/run': return self._send_json(HTTPStatus.NOT_FOUND, {'error':'not found'})
        try: payload=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))).decode('utf-8'))
        except Exception as exc: return self._send_json(HTTPStatus.BAD_REQUEST, {'error':f'invalid json: {exc}'})
        command=self._build_command(payload)
        if isinstance(command,str): return self._send_json(HTTPStatus.BAD_REQUEST, {'error':command})
        started=time.time()
        try:
            result=subprocess.run(command,cwd=WORKDIR,capture_output=True,text=True,timeout=int(payload.get('timeout_seconds',120)),check=False)
        except subprocess.TimeoutExpired:
            return self._send_json(HTTPStatus.REQUEST_TIMEOUT, {'error':'splat run timed out'})
        runtime=time.time()-started
        with LOCK:
            prev=STATS['avg_runtime_seconds']*STATS['total_runs']
            STATS['total_runs']+=1
            STATS['success_runs']+= 1 if result.returncode==0 else 0
            STATS['failed_runs']+= 1 if result.returncode!=0 else 0
            STATS['avg_runtime_seconds']=(prev+runtime)/STATS['total_runs']
            STATS['last_run_at']=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            STATS['last_returncode']=result.returncode
        self._send_json(HTTPStatus.OK, {'command':command,'command_string':shlex.join(command),'returncode':result.returncode,'runtime_seconds':runtime,'stdout':result.stdout,'stderr':result.stderr})
    def _build_command(self,payload:dict):
        if not payload.get('tx_qth'): return 'tx_qth is required'
        c=[SPLAT_BIN,'-t',str(payload['tx_qth'])]
        if payload.get('rx_qth'): c+=['-r',str(payload['rx_qth'])]
        if payload.get('output_base'): c+=['-o',str(payload['output_base'])]
        for f in payload.get('flags',[]): c.append(str(f))
        return c
    def _serve_dashboard(self):
        if not DASHBOARD.exists(): return self._send_json(HTTPStatus.NOT_FOUND, {'error':'dashboard missing'})
        data=DASHBOARD.read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def _send_json(self,status,body):
        data=json.dumps(body).encode(); self.send_response(status.value); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def log_message(self, format,*args): return

if __name__=='__main__':
    WORKDIR.mkdir(parents=True,exist_ok=True)
    print(f'Genoa sidecar listening on http://{HOST}:{PORT} (workdir={WORKDIR})')
    ThreadingHTTPServer((HOST,PORT),GenoaHandler).serve_forever()
