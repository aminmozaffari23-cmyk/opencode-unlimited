import importlib.util, os, sys

spec = importlib.util.spec_from_file_location("ouc", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

mode = sys.argv[2]
if mode == "lock-mode":                      # created under umask 077 by root
    path = sys.argv[3]
    with m.StateLock(path):
        pass
    print(oct(os.stat(path).st_mode & 0o777))
elif mode == "as-user":                      # run as an unprivileged user
    cfg = m.Config()
    writable = m.state_writable(cfg)
    print(f"writable={writable}")
    lock = os.path.join(cfg.state_dir, "state.lock")
    try:
        with m.StateLock(lock):
            print("lock=ok")
    except m.StateLockError as exc:
        print("lock=clean-error")
        print(f"msg={exc}")
