from pathlib import Path
p=Path('frontend/src/App.vue')
s=p.read_text(encoding='utf8')
old="filter(i => !['live','compare_live','strategy'].includes(i.scenario))"
assert old in s
s=s.replace(old,"filter(i => !['live','compare_live','strategy','decision'].includes(i.scenario))")
p.write_text(s,encoding='utf8')
p=Path('frontend/src/alpha/Workbench.vue')
s=p.read_text(encoding='utf8')
old='<div class="bt-actions"><a class="btn" href="#/decision">'
assert old in s
s=s.replace(old,'<div class="bt-actions"><a class="btn" href="#/market">市场观察与行业研究</a><a class="btn" href="#/decision">')
p.write_text(s,encoding='utf8')
