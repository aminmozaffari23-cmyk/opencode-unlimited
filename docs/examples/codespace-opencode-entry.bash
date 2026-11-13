# opencode-unlimited: native arguments, stdout and terminal input preserved.
# Existing .bashrc and native binary are unchanged; no global proxy exports.
unalias opencode 2>/dev/null || true
function opencode {
  command python3 -c 'import importlib.util,sys,os;s=importlib.util.spec_from_file_location("ouc_daily_entry","/usr/local/lib/opencode-unlimited/ouc.py");m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);c=m.Config();sys.exit(m.cmd_lane(sys.argv[1:],c,m.Log(os.path.join(c.state_dir,"ouc.log"))))' "$@"
}
