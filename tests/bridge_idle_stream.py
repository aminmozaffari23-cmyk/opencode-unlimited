#!/usr/bin/env python3
"""Loopback-only 22s idle stream test of the actual HTTP->SOCKS bridge.

The old bridge timeout was 20s; no provider, WARP or real quota is involved.
"""
import socket,threading,subprocess,sys,pathlib,tempfile,time,json,os,hashlib
SOURCE=pathlib.Path(sys.argv[1]).resolve();DEST=pathlib.Path(sys.argv[2]).resolve()
assert not DEST.exists()
def exact(sock,n):
 out=b''
 while len(out)<n:
  part=sock.recv(n-len(out))
  if not part:raise RuntimeError('SOCKS connection closed early')
  out+=part
 return out
with tempfile.TemporaryDirectory(prefix='ouc-bridge-idle-') as td,socket.socket() as socks:
 root=pathlib.Path(td);socks.bind(('127.0.0.1',0));socks.listen();errors=[];stop=threading.Event()
 def upstream():
  try:
   conn,_=socks.accept()
   with conn:
    assert exact(conn,3)==b'\x05\x01\x00';conn.sendall(b'\x05\x00')
    head=exact(conn,4);assert head[:3]==b'\x05\x01\x00'
    if head[3]==3:exact(conn,exact(conn,1)[0]+2)
    elif head[3]==1:exact(conn,6)
    else:exact(conn,18)
    conn.sendall(b'\x05\x00\x00\x01'+b'\0'*6)
    stop.wait(22)
    conn.sendall(b'OUC-LATE-STREAM-OK')
  except BaseException as exc:errors.append(str(exc))
 worker=threading.Thread(target=upstream,daemon=True);worker.start()
 with socket.socket() as reserve:reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
 env={k:v for k,v in os.environ.items() if not k.startswith(('OUC_','FAKE_'))}
 env.update(OUC_STATE_DIR=str(root/'state'),OUC_USER_STATE_DIR=str(root/'user-state'),OUC_TARGET_HOME=str(root/'home'),OUC_WARP_MODE='proxy',OUC_LOG_PATH=str(root/'native.log'),OUC_INSTANCE_LOCK=str(root/'instance.lock'))
 p=subprocess.Popen([sys.executable,str(SOURCE),'bridge','--listen',f'127.0.0.1:{port}','--socks',f'127.0.0.1:{socks.getsockname()[1]}'],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 conn=None;start=time.monotonic()
 try:
  end=time.monotonic()+5
  while time.monotonic()<end:
   try:conn=socket.create_connection(('127.0.0.1',port),timeout=30);break
   except OSError:time.sleep(.02)
  assert conn is not None
  conn.sendall(b'CONNECT example.invalid:443 HTTP/1.1\r\nHost: example.invalid:443\r\n\r\n')
  header=b''
  while b'\r\n\r\n' not in header:header+=conn.recv(1024)
  assert b'200 Connection Established' in header
  body=conn.recv(1024);assert body==b'OUC-LATE-STREAM-OK',(header,body,errors)
  elapsed=time.monotonic()-start;assert elapsed>=22
  result=dict(source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),loopback_only=True,
   simulated=True,provider_requests=0,warp_mutations=0,upstream_silent_seconds=22,previous_stream_timeout_seconds=20,
   late_response_received=True,seconds=round(elapsed,3),failures=0)
  DEST.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
 finally:
  stop.set()
  if conn:conn.close()
  p.terminate();p.wait(timeout=5)
  if p.stdout:p.stdout.close()
  worker.join(timeout=3)
