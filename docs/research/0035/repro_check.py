import datetime as dt, json, time
from tail_lab.api.putlab_routes import _get_configured_lake_store, universe_symbols
from tail_lab.research.backtest.signal_test import walk_forward, CONFIRMATORY_SETTING
from tail_lab.research.backtest import walk_forward as wf
t=time.time()
rule,tw,sy,k=CONFIRMATORY_SETTING
r=walk_forward(_get_configured_lake_store(),symbols=universe_symbols(),as_of=dt.date(2026,10,31),rule=rule,tenor_weeks=tw,signal_years=sy,top_k=k)
d=r.model_dump(mode="json")
bv=d["beyond_vol"]; print("mode",d["beyond_vol_mode"],"reason",d["beyond_vol_unavailable_reason"])
print({k:bv[k] for k in ("status","gamma_bar","ci90","p_value","n_periods","a_intercept","a_p_value","gamma_no_controls")})
ref=json.load(open("docs/research/0035/REAL_RESULT.json"))
print("identical:", all(bv[k]==ref[k] for k in ref if k in bv), [k for k in ref if k in bv and bv[k]!=ref[k]], [k for k in ref if k not in bv])
print("n_periods",d["n_periods"],"verdict",d["verdict"])
for k in d:
    if "episode" in k or k in ("p_value","composite"): print(k, str(d[k])[:300])
print(time.time()-t)
