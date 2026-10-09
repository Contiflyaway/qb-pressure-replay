"""QB Pressure Replay v2. Requires pandas and numpy; no Jupyter required.

GUI:  python qb_pressure_replay_v2.py
CLI:  python qb_pressure_replay_v2.py --id 2021090900 [--data DATA_FOLDER] [--no-open]
"""
from pathlib import Path
import argparse
import html
import json
import os
import sys
import threading
import webbrowser

BASE = Path(__file__).resolve().parent
FPS = 10
PRESSURE_YD = 2.0   # our own definition: a rusher within 2 yards of the QB = "pressure zone"

OUTCOMES = {"C": "Complete", "I": "Incomplete", "S": "Sack", "IN": "Interception", "R": "Scramble"}
END_LABELS = {"pass_forward": "Pass released", "qb_sack": "Sack",
              "qb_strip_sack": "Strip sack", "run": "QB run"}


def esc(value):
    return html.escape(str(value))


def mean_or_none(values):
    values = list(values)
    return float(sum(values) / len(values)) if values else None


def is_data_folder(path):
    return all((path / name).is_file() for name in
               ["games.csv", "plays.csv", "players.csv", "pffScoutingData.csv"]) and (path / "tracking").is_dir()


def find_data_folder():
    for candidate in [BASE / "data", BASE, BASE.parent / "data", BASE.parent]:
        if is_data_folder(candidate):
            return candidate
    return None


def clean_id(text):
    return text.strip().replace("tracking_", "").removesuffix(".csv")


def pff_table_html(rows):
    head = ("<table><tr><th>Rusher</th><th>Hit</th><th>Hurry</th><th>Sack</th>"
            "<th>Closest to QB (yd)</th><th>First within %.0f yd (s)</th></tr>" % PRESSURE_YD)
    body = ""
    for r in rows:
        flagged = r["pff_hit"] or r["pff_hurry"] or r["pff_sack"]
        cells = ""
        for key in ["pff_hit", "pff_hurry", "pff_sack"]:
            cells += '<td class="yes">Yes</td>' if r[key] else "<td>No</td>"
        first = "-" if r["first_within"] is None else "%.1f" % r["first_within"]
        body += '<tr%s><td>%s</td>%s<td>%.1f</td><td>%s</td></tr>' % (
            ' class="hl"' if flagged else "", esc(r["name"]), cells, r["closest_yards"], first)
    return head + body + "</table>"


def prepare_play(play_id):
    raw = tracking.loc[tracking.playId.eq(play_id)].copy()
    roles = scouting.loc[
        scouting.gameId.eq(GAME_ID) & scouting.playId.eq(play_id)
    ].copy()
    qbs = roles.loc[roles.pff_role.eq("Pass"), "nflId"].unique()
    rushers = roles.loc[roles.pff_role.eq("Pass Rush"), "nflId"].unique()
    if len(qbs) != 1 or len(rushers) == 0:
        raise ValueError("This play requires one passer and at least one pass rusher.")
    qb_id = int(qbs[0])
    rusher_ids = [int(i) for i in rushers]
    snaps = raw.loc[raw.event.eq("ball_snap"), "frameId"]
    if snaps.empty:
        raise ValueError("No official ball_snap event. Choose another play.")
    start = int(snaps.min())
    endings = raw.loc[
        raw.frameId.ge(start) & raw.event.isin(
            ["pass_forward", "qb_sack", "qb_strip_sack", "run"]
        ), ["frameId", "event"]
    ].drop_duplicates().sort_values("frameId")
    if endings.empty:
        raise ValueError("No supported release/sack/run event. Choose another play.")
    end = int(endings.iloc[0].frameId)
    end_event = str(endings.iloc[0].event)
    end_label = END_LABELS.get(end_event, end_event)
    segment = raw.loc[raw.frameId.between(start, end)].copy()
    if segment.duplicated(["frameId", "nflId"]).any():
        raise ValueError("Duplicate entity-frame keys detected.")
    timestamps = pd.to_datetime(segment.time, utc=True)
    stamp_span = (timestamps.max() - timestamps.min()).total_seconds()
    duration = (end - start) / FPS
    if abs(stamp_span - duration) > 1.1:
        raise ValueError("Frame timing disagrees with the 10 FPS assumption.")

    # Priority 2: make every play go left -> right (offense always moves toward +x)
    flipped = False
    if "playDirection" in segment.columns:
        flipped = bool(segment.playDirection.astype(str).str.lower().eq("left").any())
    if flipped:
        segment["x"] = 120 - segment["x"]
        segment["y"] = 160 / 3 - segment["y"]

    names = players.set_index("nflId").displayName.to_dict()
    role_map = roles.set_index("nflId").pff_role.to_dict()
    meta = game_plays.loc[game_plays.playId.eq(play_id)].iloc[0]
    qb_name = names.get(qb_id, "The QB")
    frame_ids = sorted(segment.frameId.unique())
    frames, distance_rows = [], []
    los, qb_y0 = None, None
    for frame_id in frame_ids:
        group = segment.loc[segment.frameId.eq(frame_id)]
        qb = group.loc[group.nflId.eq(qb_id)]
        if len(qb) != 1:
            raise ValueError("Missing or ambiguous quarterback frame.")
        q = qb.iloc[0]
        if los is None:
            ball = group.loc[group.team.eq("football")]
            los = float(ball.x.iloc[0]) if len(ball) else float(q.x)
            qb_y0 = float(q.y)
        rr = group.loc[group.nflId.isin(rushers)].copy()
        if len(rr) != len(rushers):
            raise ValueError("A pass rusher is missing from a frame.")
        rr["distance"] = np.hypot(rr.x - q.x, rr.y - q.y)
        nearest = int(rr.loc[rr.distance.idxmin(), "nflId"])
        time_s = (int(frame_id) - start) / FPS
        entities = []
        for row in group.itertuples():
            is_ball = row.team == "football"
            entity_id = None if is_ball else int(row.nflId)
            entities.append({
                "id": entity_id, "x": float(row.x), "y": float(row.y),
                "team": str(row.team),
                "name": "Football" if is_ball else names.get(entity_id, str(entity_id)),
                "number": "" if pd.isna(row.jerseyNumber) else str(int(row.jerseyNumber)),
                "role": "Football" if is_ball else role_map.get(entity_id, "Unknown")
            })
        distances = {str(int(r.nflId)): float(r.distance) for r in rr.itertuples()}
        frames.append({"frame": int(frame_id), "time": time_s,
                       "entities": entities, "distances": distances, "nearest": nearest})
        for key, value in distances.items():
            distance_rows.append({"frameId": int(frame_id), "seconds_after_snap": time_s,
                                  "nflId": int(key), "distance_yards": value})

    # PFF labels (whole play)
    pff = roles.loc[roles.nflId.isin(rushers),
                    ["nflId", "pff_hit", "pff_hurry", "pff_sack"]].copy()
    pff["nflId"] = pff.nflId.astype(int)
    for col in ["pff_hit", "pff_hurry", "pff_sack"]:
        pff[col] = pff[col].fillna(0)
    pff["name"] = pff.nflId.map(names)
    pff_idx = pff.set_index("nflId")
    if pff.pff_sack.sum() > 0:
        pff_label = "Sack"
    elif pff.pff_hit.sum() > 0:
        pff_label = "Hit"
    elif pff.pff_hurry.sum() > 0:
        pff_label = "Hurry"
    else:
        pff_label = "No PFF pressure"
    pff_pressure = bool(pff[["pff_hit", "pff_hurry", "pff_sack"]].sum().sum() > 0)

    # Priority 1: summary numbers
    dist_df = pd.DataFrame(distance_rows)
    closest = dist_df.loc[dist_df.distance_yards.idxmin()]
    near = dist_df.loc[dist_df.distance_yards <= PRESSURE_YD].sort_values("seconds_after_snap")
    first_pressure = None
    if len(near):
        fp = near.iloc[0]
        first_pressure = {"time": float(fp.seconds_after_snap), "id": int(fp.nflId),
                          "name": names.get(int(fp.nflId), str(int(fp.nflId)))}
    closest_name = names.get(int(closest.nflId), str(int(closest.nflId)))
    summary = {"closest_yards": float(closest.distance_yards), "closest_name": closest_name,
               "closest_id": int(closest.nflId), "closest_time": float(closest.seconds_after_snap),
               "first_pressure": first_pressure}

    if first_pressure:
        headline = ("%s had %.1f s before the %s. %s reached the pressure zone (%g yd) at %.1f s; "
                    "closest approach was %.1f yd by %s." % (
                        qb_name, duration, end_label.lower(), first_pressure["name"], PRESSURE_YD,
                        first_pressure["time"], summary["closest_yards"], closest_name))
    else:
        headline = ("%s had %.1f s before the %s. No rusher got within %g yd; the closest was %s at %.1f yd." % (
            qb_name, duration, end_label.lower(), PRESSURE_YD, closest_name, summary["closest_yards"]))

    rusher_stats = []
    for rid in rusher_ids:
        sub = dist_df.loc[dist_df.nflId.eq(rid)]
        within = sub.loc[sub.distance_yards <= PRESSURE_YD]
        hit = bool(pff_idx.at[rid, "pff_hit"] > 0) if rid in pff_idx.index else False
        hurry = bool(pff_idx.at[rid, "pff_hurry"] > 0) if rid in pff_idx.index else False
        sack = bool(pff_idx.at[rid, "pff_sack"] > 0) if rid in pff_idx.index else False
        rusher_stats.append({
            "id": rid, "name": names.get(rid, str(rid)),
            "closest_yards": float(sub.distance_yards.min()),
            "first_within": None if within.empty else float(within.seconds_after_snap.min()),
            "pff_hit": hit, "pff_hurry": hurry, "pff_sack": sack,
            "pff_pressure": hit or hurry or sack})

    quarter = meta["quarter"] if "quarter" in meta.index and pd.notna(meta["quarter"]) else None
    pass_result = str(meta.passResult)
    outcome_label = OUTCOMES.get(pass_result, pass_result if pass_result != "nan" else "n/a")
    desc = str(meta.playDescription)
    label = "%s%s | %s | PFF: %s" % (
        "Q%d | " % int(quarter) if quarter is not None else "",
        desc if len(desc) <= 85 else desc[:82] + "...", outcome_label, pff_label)

    payload = {"game": GAME_ID, "play": int(play_id), "qb": qb_id,
               "offense": str(meta.possessionTeam), "defense": str(meta.defensiveTeam),
               "description": desc, "outcome": pass_result, "outcome_label": outcome_label,
               "yards": float(meta.playResult), "end_event": end_event, "end_label": end_label,
               "duration": duration, "frames": frames, "label": label, "headline": headline,
               "summary": summary, "rusher_stats": rusher_stats, "pff_label": pff_label,
               "pff_pressure": pff_pressure, "flipped": flipped,
               "view": {"x0": los - 14, "x1": los + 30, "los": los, "qb_y": qb_y0},
               "rushers": [{"id": i, "name": names.get(i, str(i))} for i in rusher_ids],
               "pff_html": pff_table_html(rusher_stats)}
    return payload, dist_df, pff


HTML_TEMPLATE = r"""<!doctype html><html><head><meta charset="utf-8"><title>QB Pressure Replay</title><style>
body{font:15px Arial;background:#101827;color:#eef2ff;margin:20px}h2{margin:0 0 10px}h3{margin:24px 0 6px}
button{padding:9px 18px;margin:6px;border:0;border-radius:5px;cursor:pointer}select{padding:4px;margin-right:12px}
input{width:95%}canvas{width:100%;background:#172338;border-radius:8px}
.grid{display:grid;grid-template-columns:1.3fr 1fr;gap:14px}.note{color:#bdc8db;font-size:13px}
#status{margin:10px 0;font-weight:bold}#headline{font-size:17px;font-weight:bold;margin:10px 0}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:10px 0}
.card{background:#1b2a44;border-radius:8px;padding:10px 12px}.card .k{color:#9fb0cc;font-size:12px;text-transform:uppercase}
.card .v{font-size:24px;font-weight:bold;margin:3px 0}.card .s{color:#bdc8db;font-size:13px}
table{border-collapse:collapse;margin:6px 0}th,td{border:1px solid #33435f;padding:5px 10px;text-align:left}
th{background:#1b2a44}#gameTable th{cursor:pointer}tr.hl td{background:#3a2a1a}.yes{color:#ff8a65;font-weight:bold}
#tip{position:fixed;display:none;background:rgba(0,0,0,.85);padding:4px 8px;border-radius:4px;font-size:12px;pointer-events:none}
@media(max-width:900px){.grid,.cards{grid-template-columns:1fr}}
</style></head><body><h2>QB Pressure Replay</h2>
<label>Sort plays: <select id="sort"><option value="game">Game order</option><option value="time">Longest time to throw</option><option value="closest">Closest rusher</option><option value="arrival">Earliest pressure</option></select></label>
<label>Show: <select id="filter"><option value="all">All plays</option><option value="near">Rusher within pressure zone</option><option value="pff">PFF hit / hurry / sack</option></select></label><br><br>
<label>Select play: <select id="playSelect" style="max-width:80%"></select></label>
<div id="desc" style="margin-top:10px"></div><div id="headline"></div><div class="cards" id="cards"></div><div id="status"></div>
<button id="play">Play</button><button id="pause">Pause</button><button id="reset">Reset</button><br><input id="slider" type="range" min="0" value="0" step="1">
<div class="grid"><canvas id="field" width="780" height="560"></canvas><canvas id="chart" width="650" height="440"></canvas></div>
<p class="note">Field is zoomed to the action and flipped so the offense always moves left to right. Blue: offense. Red: defense (large dots with a colored ring: pass rushers, same color as on the chart). Yellow: quarterback. Brown: football. White ring: nearest pass rusher. Hover a dot for details. Pressure zone = within __TH__ yd of the QB (our own definition, not an official stat). Timing assumes 10 frames/second. Replay stops at release, sack, or run.</p>
<h3>Pass rushers on this play</h3><div id="pffTable"></div>
<p class="note">PFF hit / hurry / sack labels describe the whole play, not individual frames.</p>
<h3>Whole game: pass-rusher ranking (click a header to sort)</h3><div id="gameTable"></div>
<p class="note">Small sample sizes: a rusher with only a few plays should not be over-interpreted.</p>
<h3>Does the distance measure mean something?</h3><div id="valid"></div>
<p class="note">Descriptive comparison only; it does not establish causation. Dataset: regional event data (check it matches the competition dataset).</p>
<div id="tip"></div>
<script>
const DATA=__DATA__;const allPlays=DATA.plays,TH=DATA.threshold;
const PAL=['#ff9800','#00e5ff','#e040fb','#76ff03','#ff4081','#b388ff','#1de9b6','#ffd180'];
let d=null,i=0,timer=null,order=[],hits=[];
const $=id=>document.getElementById(id);const slider=$('slider'),select=$('playSelect');
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
function lastName(n){const p=n.split(' ');let l=p[p.length-1];if(/^(Jr\.?|Sr\.?|II|III|IV)$/.test(l)&&p.length>1)l=p[p.length-2];return l;}
function colorOf(id){const k=d.rushers.findIndex(r=>r.id===id);return PAL[(k<0?0:k)%PAL.length];}
function card(k,v,s){return `<div class="card"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div></div>`;}
function buildOrder(){
 const f=$('filter').value,s=$('sort').value;
 let idx=allPlays.map((p,k)=>k).filter(k=>{const p=allPlays[k];if(f==='pff')return p.pff_pressure;if(f==='near')return p.summary.first_pressure!==null;return true;});
 const keys={time:k=>-allPlays[k].duration,closest:k=>allPlays[k].summary.closest_yards,arrival:k=>{const a=allPlays[k].summary.first_pressure;return a===null?1e9:a.time;}};
 if(keys[s])idx.sort((a,b)=>keys[s](a)-keys[s](b));
 order=idx;select.innerHTML='';
 order.forEach(k=>{const o=document.createElement('option');o.value=k;o.textContent=allPlays[k].label;select.appendChild(o);});
 selectPlay();}
function selectPlay(){
 clearInterval(timer);timer=null;i=0;
 if(!order.length){d=null;$('desc').textContent='No plays match this filter.';$('headline').textContent='';$('cards').innerHTML='';$('status').textContent='';$('pffTable').innerHTML='';
  $('field').getContext('2d').clearRect(0,0,780,560);$('chart').getContext('2d').clearRect(0,0,650,440);return;}
 d=allPlays[Number(select.value)];slider.max=d.frames.length-1;
 $('desc').textContent=`Game ${d.game} | Play ${d.play} | ${d.offense} vs ${d.defense} | Result: ${d.outcome_label} | Yards: ${d.yards}. ${d.description}`;
 $('headline').textContent=d.headline;
 const S=d.summary,fp=S.first_pressure;
 $('cards').innerHTML=card('Time to '+d.end_label.toLowerCase(),d.duration.toFixed(1)+' s','after the snap')
  +card('Closest rusher to QB',S.closest_yards.toFixed(1)+' yd',S.closest_name+' at '+S.closest_time.toFixed(1)+' s')
  +card('Pressure arrival (within '+TH+' yd)',fp?fp.time.toFixed(1)+' s':'None',fp?fp.name+' got within '+TH+' yd':'No rusher got within '+TH+' yd')
  +card('Result',d.outcome_label,d.yards+' yd | PFF: '+d.pff_label);
 $('pffTable').innerHTML=d.pff_html;render();}
function render(){
 if(!d)return;const f=d.frames[i];slider.value=i;const S=d.summary;
 const nr=d.rushers.find(r=>r.id===f.nearest);
 $('status').textContent=`${f.time.toFixed(1)} s after snap | Frame ${f.frame} | Nearest rusher: ${nr.name} (${f.distances[String(f.nearest)].toFixed(2)} yd) | Endpoint: ${d.end_label}`;
 const c=$('field'),ctx=c.getContext('2d'),W=c.width,H=c.height;ctx.clearRect(0,0,W,H);
 const x0=d.view.x0,x1=d.view.x1,s=(W-50)/(x1-x0),half=(H-60)/s/2;
 let yc=Math.min(Math.max(d.view.qb_y,half),53.3-half);if(half>26.65)yc=26.65;const ylo=yc-half;
 const px=x=>25+(x-x0)*s,py=y=>H-30-(y-ylo)*s;
 ctx.fillStyle='#194f3a';ctx.fillRect(25,30,W-50,H-60);
 ctx.save();ctx.beginPath();ctx.rect(25,30,W-50,H-60);ctx.clip();
 ctx.lineWidth=1;ctx.strokeStyle='rgba(160,200,175,0.45)';
 for(let x=Math.ceil(x0/5)*5;x<=x1;x+=5){ctx.beginPath();ctx.moveTo(px(x),30);ctx.lineTo(px(x),H-30);ctx.stroke();}
 ctx.strokeStyle='#fff';ctx.lineWidth=2;[0,53.3].forEach(y=>{ctx.beginPath();ctx.moveTo(25,py(y));ctx.lineTo(W-25,py(y));ctx.stroke();});
 ctx.strokeStyle='#ffd54f';ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(px(d.view.los),30);ctx.lineTo(px(d.view.los),H-30);ctx.stroke();
 ctx.fillStyle='#ffd54f';ctx.font='bold 12px Arial';ctx.fillText('Line of scrimmage',px(d.view.los)+5,46);
 const rank=e=>e.role==='Football'?5:(e.id===d.qb?4:(e.role==='Pass Rush'?3:1));
 const qb=f.entities.find(e=>e.id===d.qb),nrE=f.entities.find(e=>e.id===f.nearest);
 ctx.strokeStyle='#fff';ctx.lineWidth=1;ctx.setLineDash([5,4]);ctx.beginPath();ctx.moveTo(px(qb.x),py(qb.y));ctx.lineTo(px(nrE.x),py(nrE.y));ctx.stroke();ctx.setLineDash([]);
 hits=[];
 for(const e of [...f.entities].sort((a,b)=>rank(a)-rank(b))){
  const X=px(e.x),Y=py(e.y),isQB=e.id===d.qb,isR=e.role==='Pass Rush',isB=e.role==='Football';
  let col,r;
  if(isB){col='#c88b55';r=5;}else if(isQB){col='#ffeb3b';r=9;}else if(isR){col='#f56b72';r=8;}else{col=e.team===d.offense?'#4299ff':'#f56b72';r=6;}
  ctx.beginPath();ctx.arc(X,Y,r,0,7);ctx.fillStyle=col;ctx.fill();
  if(isR){ctx.lineWidth=3;ctx.strokeStyle=colorOf(e.id);ctx.stroke();}
  if(isR&&e.id===f.nearest){ctx.beginPath();ctx.arc(X,Y,r+6,0,7);ctx.lineWidth=2;ctx.strokeStyle='#fff';ctx.stroke();}
  if(isR||isQB){ctx.fillStyle='#fff';ctx.font='bold 12px Arial';ctx.fillText(isQB?'QB':lastName(e.name),X+12,Y-6);}
  let t=isB?'Football':`${e.name}${e.number?' #'+e.number:''} | ${e.role} | ${e.team===d.offense?'offense':'defense'}`;
  if(isR)t+=` | ${f.distances[String(e.id)].toFixed(1)} yd from QB`;
  hits.push({x:X,y:Y,t:t});}
 ctx.restore();
 ctx.fillStyle='#fff';ctx.font='13px Arial';ctx.textAlign='center';ctx.fillText('Offense moves left to right  (yards downfield)',W/2,20);
 for(let x=Math.ceil(x0/10)*10;x<=x1;x+=10){if(x<10||x>110)continue;const n=x-10;ctx.fillText(String(n<=50?n:100-n),px(x),H-10);}
 ctx.textAlign='left';
 // chart
 const z=$('chart').getContext('2d');z.clearRect(0,0,650,440);
 const maxY=Math.max(5,...d.frames.flatMap(ff=>Object.values(ff.distances)))*1.1;
 const tx=t=>55+t/Math.max(d.duration,.1)*565,ty=v=>290-v/maxY*245;
 z.textAlign='left';z.font='13px Arial';z.fillStyle='#fff';z.fillText('Pass-rusher distance to QB (yards)',80,22);
 z.fillStyle='rgba(255,82,82,0.13)';z.fillRect(55,ty(TH),565,290-ty(TH));
 z.strokeStyle='#65748b';z.lineWidth=1;z.beginPath();z.moveTo(55,40);z.lineTo(55,290);z.lineTo(620,290);z.stroke();
 z.fillStyle='#fff';for(let k=0;k<=4;k++){const v=maxY*k/4;z.fillText(v.toFixed(1),8,ty(v)+4);const tt=d.duration*k/4;z.fillText(tt.toFixed(1),tx(tt)-8,310);}
 z.fillText('Seconds after snap',250,330);
 z.strokeStyle='#ff5252';z.lineWidth=1.5;z.setLineDash([6,4]);z.beginPath();z.moveTo(55,ty(TH));z.lineTo(620,ty(TH));z.stroke();z.setLineDash([]);
 z.fillStyle='#ff5252';z.fillText('Pressure zone ('+TH+' yd)',480,ty(TH)-5);
 d.rushers.forEach((r,k)=>{const key=r.id===S.closest_id;z.globalAlpha=key?1:0.45;z.strokeStyle=PAL[k%PAL.length];z.lineWidth=key?4:1.5;z.beginPath();
  d.frames.forEach((ff,j)=>{const v=ff.distances[String(r.id)];if(j===0)z.moveTo(tx(ff.time),ty(v));else z.lineTo(tx(ff.time),ty(v));});z.stroke();z.globalAlpha=1;});
 if(S.first_pressure){z.fillStyle='#fff';z.beginPath();z.arc(tx(S.first_pressure.time),ty(TH),5,0,7);z.fill();}
 z.strokeStyle='#ffa726';z.lineWidth=2;z.beginPath();z.moveTo(tx(d.duration),40);z.lineTo(tx(d.duration),290);z.stroke();
 z.fillStyle='#ffa726';z.textAlign='right';z.fillText(d.end_label,tx(d.duration)-4,52);z.textAlign='left';
 d.rushers.forEach((r,k)=>{const key=r.id===S.closest_id;z.fillStyle=PAL[k%PAL.length];z.font=(key?'bold ':'')+'11px Arial';
  z.fillText(r.name+(key?' (closest)':''),65+(k%2)*280,352+Math.floor(k/2)*16);});
 z.font='13px Arial';z.strokeStyle='#fff';z.lineWidth=1;z.setLineDash([4,4]);z.beginPath();z.moveTo(tx(f.time),40);z.lineTo(tx(f.time),290);z.stroke();z.setLineDash([]);}
$('field').onmousemove=ev=>{const c=$('field'),r=c.getBoundingClientRect(),k=c.width/r.width,mx=(ev.clientX-r.left)*k,my=(ev.clientY-r.top)*k;
 let best=null,bd=16*16;for(const h of hits){const dd=(h.x-mx)**2+(h.y-my)**2;if(dd<bd){bd=dd;best=h;}}
 const tip=$('tip');if(best){tip.style.display='block';tip.style.left=(ev.clientX+12)+'px';tip.style.top=(ev.clientY+12)+'px';tip.textContent=best.t;}else tip.style.display='none';};
$('field').onmouseleave=()=>{$('tip').style.display='none';};
// whole-game table
const COLS=[['name','Rusher'],['plays','Plays'],['avg_closest','Avg closest (yd)'],['pct_within','% plays within '+TH+' yd'],['avg_arrival','Avg arrival (s)'],['pff_plays','Plays with PFF hit/hurry/sack']];
let sk='pct_within',sd=-1;
function renderGame(){
 const rows=[...DATA.rushers];
 rows.sort((a,b)=>{const x=a[sk],y=b[sk];if(x===null)return 1;if(y===null)return -1;return typeof x==='string'?sd*x.localeCompare(y):sd*(x-y);});
 const f=(v,n)=>v===null?'-':v.toFixed(n);
 $('gameTable').innerHTML='<table><tr>'+COLS.map(c=>`<th data-k="${c[0]}">${c[1]}${c[0]===sk?(sd<0?' &#9660;':' &#9650;'):''}</th>`).join('')+'</tr>'
  +rows.map(r=>`<tr><td>${esc(r.name)}</td><td>${r.plays}</td><td>${f(r.avg_closest,1)}</td><td>${f(r.pct_within,0)}%</td><td>${f(r.avg_arrival,1)}</td><td>${r.pff_plays}</td></tr>`).join('')+'</table>';
 $('gameTable').querySelectorAll('th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;if(k===sk)sd=-sd;else{sk=k;sd=k==='name'?1:-1;}renderGame();});}
function renderValid(){
 const V=DATA.validation,f=(v,n)=>v===null?'-':v.toFixed(n);
 $('valid').innerHTML='<table><tr><th>Group of plays</th><th>Plays</th><th>With PFF hit/hurry/sack</th><th>Share</th></tr>'
  +V.groups.map(g=>`<tr><td>${esc(g.group)}</td><td>${g.plays}</td><td>${g.pff_plays}</td><td>${f(g.pff_pct,0)}${g.pff_pct===null?'':'%'}</td></tr>`).join('')+'</table>'
  +`<p>Average closest rusher distance: ${f(V.avg_closest_pff,1)} yd on plays with a PFF pressure label vs ${f(V.avg_closest_nopff,1)} yd on plays without.</p>`;}
$('sort').onchange=buildOrder;$('filter').onchange=buildOrder;select.onchange=selectPlay;
slider.oninput=()=>{clearInterval(timer);timer=null;i=Number(slider.value);render();};
$('play').onclick=()=>{if(!d||timer)return;if(i===d.frames.length-1)i=0;timer=setInterval(()=>{render();if(i>=d.frames.length-1){clearInterval(timer);timer=null;}else i++;},100);};
$('pause').onclick=()=>{clearInterval(timer);timer=null;};
$('reset').onclick=()=>{clearInterval(timer);timer=null;i=0;render();};
renderGame();renderValid();buildOrder();
</script></body></html>"""


def build_replay(data_folder, tracking_id, output_folder):
    global np, pd
    import numpy as np
    import pandas as pd
    global games, plays, players, scouting, tracking, game_plays, GAME_ID
    GAME_ID = int(tracking_id)
    tracking_path = data_folder / "tracking" / f"tracking_{GAME_ID}.csv"
    if not tracking_path.is_file():
        raise FileNotFoundError(f"Tracking file not found: {tracking_path.name}")
    games = pd.read_csv(data_folder / "games.csv")
    plays = pd.read_csv(data_folder / "plays.csv")
    players = pd.read_csv(data_folder / "players.csv")
    scouting = pd.read_csv(data_folder / "pffScoutingData.csv")
    if plays.duplicated(["gameId", "playId"]).any() or scouting.duplicated(["gameId", "playId", "nflId"]).any():
        raise ValueError("Duplicate source keys detected.")
    tracking = pd.read_csv(tracking_path)
    game_plays = plays.loc[plays.gameId.eq(GAME_ID)].copy()
    payloads, rejected = [], []
    for play_id in game_plays.playId:
        try:
            payload, _, _ = prepare_play(int(play_id))
            payloads.append(payload)
        except ValueError as error:
            rejected.append({"playId": int(play_id), "reason": str(error)})
    if not payloads:
        raise ValueError("No supported plays found in this game.")

    # Priority 5: whole-game rusher table
    agg = {}
    for p in payloads:
        for r in p["rusher_stats"]:
            a = agg.setdefault(r["id"], {"id": r["id"], "name": r["name"], "plays": 0,
                                         "closest": [], "within": 0, "arrivals": [], "pff_plays": 0})
            a["plays"] += 1
            a["closest"].append(r["closest_yards"])
            if r["first_within"] is not None:
                a["within"] += 1
                a["arrivals"].append(r["first_within"])
            if r["pff_pressure"]:
                a["pff_plays"] += 1
    rusher_rows = [{"id": a["id"], "name": a["name"], "plays": a["plays"],
                    "avg_closest": mean_or_none(a["closest"]),
                    "pct_within": 100.0 * a["within"] / a["plays"],
                    "avg_arrival": mean_or_none(a["arrivals"]),
                    "pff_plays": a["pff_plays"]} for a in agg.values()]

    # Priority 6: does "within 2 yd" line up with PFF labels?
    def group(name, ps):
        n = len(ps)
        k = sum(1 for p in ps if p["pff_pressure"])
        return {"group": name, "plays": n, "pff_plays": k, "pff_pct": None if n == 0 else 100.0 * k / n}
    near = [p for p in payloads if p["summary"]["first_pressure"] is not None]
    far = [p for p in payloads if p["summary"]["first_pressure"] is None]
    validation = {
        "groups": [group("A rusher got within %g yd of the QB" % PRESSURE_YD, near),
                   group("No rusher got within %g yd" % PRESSURE_YD, far)],
        "avg_closest_pff": mean_or_none(p["summary"]["closest_yards"] for p in payloads if p["pff_pressure"]),
        "avg_closest_nopff": mean_or_none(p["summary"]["closest_yards"] for p in payloads if not p["pff_pressure"]),
    }

    data = {"plays": payloads, "rushers": rusher_rows, "validation": validation,
            "game": GAME_ID, "threshold": PRESSURE_YD}
    output_folder.mkdir(parents=True, exist_ok=True)
    document = (HTML_TEMPLATE
                .replace("__TH__", "%g" % PRESSURE_YD)
                .replace("__DATA__", json.dumps(data, allow_nan=False).replace("<", "\\u003c")))
    path = output_folder / f"QB_Replay_{GAME_ID}.html"
    path.write_text(document, encoding="utf-8")
    pd.DataFrame(rejected, columns=["playId", "reason"]).to_csv(
        output_folder / f"excluded_{GAME_ID}.csv", index=False
    )
    return path, len(payloads)


def open_file(path):
    """Open the HTML with the default program. os.startfile is more reliable than webbrowser on Windows/Edge."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path.resolve()))
        else:
            webbrowser.open(path.resolve().as_uri())
        return True
    except Exception:
        return False


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    print("Starting QB Pressure Replay window...", flush=True)
    root = tk.Tk()
    root.title("QB Pressure Replay")
    root.geometry("620x310")
    root.minsize(540, 290)
    root.lift()
    root.attributes("-topmost", True)
    root.after(1500, lambda: root.attributes("-topmost", False))
    folder = find_data_folder()
    folder_var = tk.StringVar(value=str(folder) if folder else "")
    id_var = tk.StringVar(value="2021090900")
    status_var = tk.StringVar(value="Enter a tracking ID, then click Open Replay.")
    ttk.Label(root, text="QB Pressure Replay", font=("Arial", 18, "bold")).pack(pady=12)
    ttk.Label(root, text="Data folder (automatically detected when placed beside the script)").pack()
    row = ttk.Frame(root)
    row.pack(fill="x", padx=18, pady=6)
    ttk.Entry(row, textvariable=folder_var).pack(side="left", fill="x", expand=True)

    def browse():
        chosen = filedialog.askdirectory(title="Select the folder containing the four CSV files and tracking")
        if chosen:
            folder_var.set(chosen)
    ttk.Button(row, text="Browse", command=browse).pack(side="right", padx=5)
    ttk.Label(root, text="Tracking ID or filename").pack(pady=(6, 0))
    entry = ttk.Entry(root, textvariable=id_var, width=35)
    entry.pack(pady=6)
    entry.focus_set()
    ttk.Label(root, textvariable=status_var, wraplength=580).pack(pady=8)
    results = []

    def finish():
        if not results:
            root.after(100, finish)
            return
        ok, result = results.pop()
        button.config(state="normal")
        if ok:
            path, count = result
            status_var.set(f"Ready: {count} plays. Opening replay in your browser.")
            if not open_file(path):
                messagebox.showinfo("Replay saved", f"Open this file in a browser:\n{path}")
        else:
            status_var.set("Unable to generate replay. Check the folder and tracking ID.")
            messagebox.showerror("Replay error", str(result))

    def run():
        data = Path(folder_var.get())
        if not is_data_folder(data):
            messagebox.showerror("Data folder", "Choose a folder containing games.csv, plays.csv, players.csv, pffScoutingData.csv and tracking.")
            return
        text = clean_id(id_var.get())
        if not text.isdigit():
            messagebox.showerror("Tracking ID", "Enter a numeric tracking ID, for example 2021090900.")
            return
        button.config(state="disabled")
        status_var.set("Loading and validating plays. Please wait...")

        def worker():
            try:
                results.append((True, build_replay(data, text, BASE / "qb_pressure_outputs")))
            except Exception as error:
                results.append((False, error))
        threading.Thread(target=worker, daemon=True).start()
        root.after(100, finish)
    button = ttk.Button(root, text="Open Replay", command=run)
    button.pack(pady=5)
    root.bind("<Return>", lambda event: run() if str(button["state"]) != "disabled" else None)
    root.mainloop()


def run_cli(args):
    data = Path(args.data) if args.data else find_data_folder()
    if data is None or not is_data_folder(data):
        sys.exit("Data folder not found. Use --data to point to the folder with games.csv, plays.csv, "
                 "players.csv, pffScoutingData.csv and tracking/.")
    text = clean_id(args.id)
    if not text.isdigit():
        sys.exit("--id must be numeric, for example 2021090900.")
    out = Path(args.out) if args.out else BASE / "qb_pressure_outputs"
    path, count = build_replay(data, text, out)
    print(f"Done: {count} plays written to {path}")
    if not args.no_open and not open_file(path):
        print("Could not open a browser automatically. Open the HTML file manually.")


def main():
    parser = argparse.ArgumentParser(description="QB Pressure Replay")
    parser.add_argument("--id", help="tracking/game ID, e.g. 2021090900 (skips the window)")
    parser.add_argument("--data", help="data folder (default: auto-detect)")
    parser.add_argument("--out", help="output folder (default: qb_pressure_outputs next to the script)")
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = parser.parse_args()
    if args.id:
        run_cli(args)
    else:
        run_gui()


if __name__ == "__main__":
    main()