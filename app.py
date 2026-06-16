# ============================================================
#  Task 6 — Smart Energy Meter System
#  Tech: Python, Flask, CSV storage
#  Simulates: Arduino + ACS712 (current) + Voltage divider
#             + SIM900 GSM + LCD display
#  Exact math from Project-Code-Report.c used throughout
# ============================================================

from flask import Flask, render_template, jsonify, request, Response
import csv, math, random, datetime, os, time, io

app = Flask(__name__)

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'data', 'energy_log.csv')
os.makedirs(os.path.join(BASE_DIR, 'data'), exist_ok=True)

MV_PER_AMP   = 66
POWER_FACTOR = 0.3099

def calc_vrms(vpp):   return (vpp / 2.0) * 0.707 * 575
def calc_irms(ipp):   return (ipp / 2.0) * 0.707 * 1000 / MV_PER_AMP
def calc_power(v,i):  return v * i * POWER_FACTOR
def calc_wh(power):   return power / 3600

def get_tariff_rate(sum_wh):
    kwh = sum_wh / 1000
    if kwh <= 50:   return 3.15
    if kwh <= 100:  return 3.60
    if kwh <= 250:  return 4.25
    return 5.20

def calc_rupees(wh, sum_wh):
    return wh * get_tariff_rate(sum_wh) / 1000

state = {
    'sum_wh':0.0,'sum_rupees':0.0,'start_time':time.time(),
    'gsm_status':'Connected','sms_sent':0,'load_profile':'Home'
}

LOADS = {
    'Home'    :{'base_power':500,  'variation':300,  'voltage':230},
    'Office'  :{'base_power':2000, 'variation':800,  'voltage':230},
    'Industry':{'base_power':8000, 'variation':3000, 'voltage':415},
}

def simulate_meter():
    now  = datetime.datetime.now()
    hour = now.hour + now.minute/60
    load = LOADS[state['load_profile']]
    lf   = 0.3 + 0.7*abs(math.sin((hour-6)*math.pi/12))
    tp   = max(50, load['base_power']*lf + random.gauss(0, load['variation']*0.1))
    vrms_t = load['voltage'] + random.uniform(-5,5)
    irms_t = tp / (vrms_t * abs(POWER_FACTOR) + 0.001)
    vpp = max(0.001, (vrms_t/(0.707*575))*2.0 + random.uniform(-0.001,0.001))
    ipp = max(0.001, (irms_t/(0.707*1000/MV_PER_AMP))*2.0 + random.uniform(-0.001,0.001))
    vrms  = calc_vrms(vpp)
    irms  = calc_irms(ipp)
    power = calc_power(vrms, irms)
    wh    = calc_wh(power)
    state['sum_wh']     += wh
    rupees               = calc_rupees(wh, state['sum_wh'])
    state['sum_rupees'] += rupees
    pf   = POWER_FACTOR + random.uniform(-0.02,0.02)
    freq = 50.0 + random.uniform(-0.1,0.1)
    kwh  = state['sum_wh']/1000
    rate = get_tariff_rate(state['sum_wh'])
    if kwh<=50:   slab="Slab 1 (0-50 kWh @ ₹3.15)"
    elif kwh<=100:slab="Slab 2 (50-100 kWh @ ₹3.60)"
    elif kwh<=250:slab="Slab 3 (100-250 kWh @ ₹4.25)"
    else:         slab="Slab 4 (>250 kWh @ ₹5.20)"
    return {
        'timestamp':now.strftime('%Y-%m-%d %H:%M:%S'),
        'date':now.strftime('%Y-%m-%d'),'time':now.strftime('%H:%M:%S'),
        'vrms':round(vrms,2),'irms':round(irms,4),
        'power':round(power,3),'wh':round(wh,6),
        'sum_wh':round(state['sum_wh'],4),'kwh':round(kwh,4),
        'rupees':round(rupees,4),'sum_rupees':round(state['sum_rupees'],4),
        'power_factor':round(pf,3),'frequency':round(freq,2),
        'tariff_rate':rate,'tariff_slab':slab,
        'load_profile':state['load_profile'],
        'gsm_status':state['gsm_status'],'sms_sent':state['sms_sent'],
        'uptime':int(time.time()-state['start_time']),
        'lcd_line1':f"{vrms:.1f}v  {irms:.3f}A",
        'lcd_line2':f"{power:.2f}w",'vpp':round(vpp,5),'ipp':round(ipp,5),
    }

FIELDS = ['timestamp','date','time','vrms','irms','power','wh',
          'sum_wh','kwh','rupees','sum_rupees','power_factor',
          'frequency','tariff_rate','load_profile']

def log_reading(data):
    exists = os.path.isfile(DATA_FILE)
    with open(DATA_FILE,'a',newline='') as f:
        w = csv.DictWriter(f,fieldnames=FIELDS)
        if not exists: w.writeheader()
        w.writerow({k:data[k] for k in FIELDS})

def read_logs(n=200):
    if not os.path.exists(DATA_FILE): return []
    with open(DATA_FILE,'r') as f:
        return list(csv.DictReader(f))[-n:]

def get_stats(rows):
    if not rows: return {}
    powers=[float(r['power']) for r in rows]
    vrms=[float(r['vrms']) for r in rows]
    kwhs=[float(r['kwh']) for r in rows]
    return {
        'count':len(rows),'power_avg':round(sum(powers)/len(powers),2),
        'power_max':round(max(powers),2),'power_min':round(min(powers),2),
        'vrms_avg':round(sum(vrms)/len(vrms),2),
        'total_kwh':round(max(kwhs),4),'total_rs':round(state['sum_rupees'],2),
        'first':rows[0]['timestamp'],'last':rows[-1]['timestamp'],
    }

def seed_data():
    if os.path.exists(DATA_FILE) and os.path.getsize(DATA_FILE)>200: return
    base=datetime.datetime.now()-datetime.timedelta(hours=12)
    rwh=0.0; rrs=0.0
    for i in range(144):
        t=base+datetime.timedelta(minutes=i*5); hour=t.hour+t.minute/60
        lf=0.3+0.7*abs(math.sin((hour-6)*math.pi/12))
        tp=max(50,500*lf+random.gauss(0,30))
        vt=230+random.uniform(-5,5); it=tp/(vt*0.31)
        vpp=(vt/(0.707*575))*2.0; ipp=(it/(0.707*1000/MV_PER_AMP))*2.0
        vv=calc_vrms(vpp); iv=calc_irms(ipp)
        pw=calc_power(vv,iv); wh=calc_wh(pw)*300
        rwh+=wh; rs=calc_rupees(wh,rwh); rrs+=rs
        row={
            'timestamp':t.strftime('%Y-%m-%d %H:%M:%S'),
            'date':t.strftime('%Y-%m-%d'),'time':t.strftime('%H:%M:%S'),
            'vrms':round(vv,2),'irms':round(iv,4),'power':round(pw,3),
            'wh':round(wh,6),'sum_wh':round(rwh,4),'kwh':round(rwh/1000,4),
            'rupees':round(rs,4),'sum_rupees':round(rrs,4),
            'power_factor':round(0.31+random.uniform(-0.02,0.02),3),
            'frequency':round(50+random.uniform(-0.1,0.1),2),
            'tariff_rate':get_tariff_rate(rwh),'load_profile':'Home'
        }
        log_reading(row)
    state['sum_wh']=rwh; state['sum_rupees']=rrs

@app.route('/')
def index(): return render_template('dashboard.html')

@app.route('/api/reading')
def api_reading():
    data=simulate_meter(); log_reading(data)
    rows=read_logs(); stats=get_stats(rows)
    alerts=[]
    if data['power']>5000:
        alerts.append({'type':'danger','msg':f"⚡ HIGH LOAD: {data['power']:.0f}W — Check appliances!"})
    if data['vrms']<200 or data['vrms']>260:
        alerts.append({'type':'warning','msg':f"⚠️ VOLTAGE {data['vrms']:.1f}V — Fluctuation detected!"})
    if data['sum_rupees']>1000:
        alerts.append({'type':'warning','msg':f"💸 Bill Rs.{data['sum_rupees']:.2f} — High consumption!"})
    return jsonify({'reading':data,'stats':stats,'alerts':alerts,'total_logs':len(rows)})

@app.route('/api/history')
def api_history():
    rows=read_logs(60)
    return jsonify({
        'labels':[r['time'] for r in rows],
        'power':[float(r['power']) for r in rows],
        'vrms':[float(r['vrms']) for r in rows],
        'irms':[float(r['irms']) for r in rows],
        'kwh':[float(r['kwh']) for r in rows],
        'rupees':[float(r['sum_rupees']) for r in rows],
    })

@app.route('/api/logs')
def api_logs():
    rows=read_logs(50)
    return jsonify({'logs':list(reversed(rows))})

@app.route('/api/set_load',methods=['POST'])
def api_set_load():
    p=request.json.get('profile','Home')
    if p in LOADS: state['load_profile']=p
    return jsonify({'profile':state['load_profile']})

@app.route('/api/send_sms',methods=['POST'])
def api_send_sms():
    state['sms_sent']+=1
    msg=(f"Dear Customer, Your Energy Consumption is: "
         f"{state['sum_wh']:.2f} Wh ({state['sum_wh']/1000:.3f} kWh) "
         f"and Total Billing is Rs. {state['sum_rupees']:.2f}")
    return jsonify({'status':'sent','message':msg,'sms_count':state['sms_sent']})

@app.route('/api/reset_meter',methods=['POST'])
def api_reset():
    state['sum_wh']=0.0; state['sum_rupees']=0.0
    state['start_time']=time.time(); state['sms_sent']=0
    if os.path.exists(DATA_FILE): os.remove(DATA_FILE)
    return jsonify({'status':'reset'})

@app.route('/api/export')
def api_export():
    rows=read_logs(5000); out=io.StringIO()
    if rows:
        w=csv.DictWriter(out,fieldnames=rows[0].keys())
        w.writeheader(); w.writerows(rows)
    return Response(out.getvalue(),mimetype='text/csv',
        headers={'Content-Disposition':'attachment;filename=energy_log.csv'})

if __name__=='__main__':
    seed_data()
    print("="*55)
    print("  SMART ENERGY METER SYSTEM")
    print("  Arduino + ACS712 + Voltage Sensor + SIM900 GSM")
    print("="*55)
    print("  Open browser: http://127.0.0.1:5000")
    print("="*55)
    app.run(debug=True,host='0.0.0.0',port=5000)