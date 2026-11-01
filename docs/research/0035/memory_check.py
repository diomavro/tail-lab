import sys, resource
sys.path.insert(0, "tests")
from test_research_backtest_beyond_vol import _panel
from tail_lab.research.backtest.beyond_vol import beyond_vol_test
T = int(sys.argv[1])
base = (_panel(seed=2, effect=0.0) * 20)
import dataclasses, datetime as dt
ps = [dataclasses.replace(p, date=dt.date(2000,1,1)+dt.timedelta(days=28*i)) if hasattr(p,'date') else p for i,p in enumerate(base[:T])]
b = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
r = beyond_vol_test(ps)
print(T, len(ps), r.status, "baseline MB", b//1024, "peak MB", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024)
