# ============================================================
#  Task 6 — Smart Energy Meter System
#  Tech: Python, Flask, CSV storage
#  Simulates: Arduino + ACS712 Current Sensor + Voltage Divider
#             + LCD + SIM900 GSM Module
#  Exact formulas from Project-Code-Report.c used
# ============================================================

from flask import Flask, render_template, jsonify, request, Response
import csv, math, random, datetime, os, time, io

app = Flask(__name__)

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'data', 'energy_log.csv')
os.makedirs(os.path.join(BASE_DIR, 'data'), exist_ok=True)

# ════════════════════════════════════════════
#  ARDUINO CONSTANTS (from Project-Code-Report.c)
# ════════════════════════════════════════════
MV_PER_AMP   = 66      # 66 for 30A ACS712 module
POWER_FACTOR = 0.3099  # from original code: power = vrms * irms * 0.3099
TARIFF_SLAB  = [       # getReading() tariff slabs (Rs. per WH)
    (50,   3.15),
    (100,  3.60),
    (250,  4.25),
    (None, 5.20),
]

# Global energy accumulators (mirrors sumWH, sumRupees in .c file)
meter_state = {
    'sum_wh'      : 0.0,
    'sum_rupees'  : 0.0,
    'sum_kwh'     : 0.0,
    'peak_power'  : 0.0,
    'start_time'  : time.time(),
    'last_bill_time': None,
    'gsm_log'     : [],          # SMS bill log
    'appliances'  : {            # Simulated home appliances
        'AC'       : {'watts':1500, 'on':True},
        'Fridge'   : {'watts':150,  'on':True},
        'TV'       : {'watts':100,  'on':True},
        'Lights'   : {'watts':200,  'on':True},
        'Fan'      : {'watts':75,   'on':False},
        'Washing M': {'watts':500,  'on':False},
    }
}

# ════════════════════════════════════════════
#  SENSOR SIMULATION
#  Mirrors getVPP(), energyCalculations() from .c file
# ════════════════════════════════════════════
def simulate_energy():
    """Simulate ACS712 + Voltage divider readings — exact .c formulas."""
    now  = datetime.datetime.now()
    hour = now.hour + now.minute / 60

    # --- Voltage simulation (AC mains) ---
    # vrms = (Voltage / 2.0) * 0.707 * 575
    base_vrms = 220 + 10 * math.sin(hour * math.pi / 12) + random.uniform(-2, 2)
    base_vrms = max(200, min(240, base_vrms))

    # --- Load-based current simulation ---
    # Total load from active appliances
    active_watts = sum(
        a['watts'] for a in meter_state['appliances'].values() if a['on']
    )
    # irms = (current / 2.0) * 0.707 * 1000 / mVperAmp
    base_irms = (active_watts / base_vrms) + random.uniform(-0.05, 0.05)
    base_irms = max(0.01, base_irms)

    # --- Power (from .c: power = vrms * irms * 0.3099) ---
    power = base_vrms * base_irms * POWER_FACTOR

    # --- Energy per reading (WH) ---
    wh = power / 3600.0

    # --- Accumulators ---
    meter_state['sum_wh']    += wh
    meter_state['sum_kwh']    = meter_state['sum_wh'] / 1000.0

    # --- Tariff / Rupees (getReading() logic from .c file) ---
    sum_wh = meter_state['sum_wh']
    if sum_wh <= 50:
        rupees = wh * 3.15
    elif sum_wh <= 100:
        rupees = wh * 3.60
    elif sum_wh <= 250:
        rupees = wh * 4.25
    else:
        rupees = wh * 5.20
    meter_state['sum_rupees'] += rupees

    # Track peak power
    if power > meter_state['peak_power']:
        meter_state['peak_power'] = round(power, 2)

    # Apparent power (VA) and power factor
    apparent = base_vrms * base_irms
    pf       = round(power / apparent, 3) if apparent > 0 else 0

    # CO2 emissions (0.82 kg CO2 per kWh — India grid average)
    co2_kg = round(meter_state['sum_kwh'] * 0.82, 4)

    return {
        'timestamp'   : now.strftime('%Y-%m-%d %H:%M:%S'),
        'date'        : now.strftime('%Y-%m-%d'),
        'time'        : now.strftime('%H:%M:%S'),
        'voltage'     : round(base_vrms, 2),
        'current'     : round(base_irms, 3),
        'power'       : round(power, 2),
        'power_factor': pf,
        'wh'          : round(wh, 5),
        'kwh'         : round(meter_state['sum_kwh'], 4),
        'sum_wh'      : round(meter_state['sum_wh'], 3),
        'sum_rupees'  : round(meter_state['sum_rupees'], 2),
        'peak_power'  : meter_state['peak_power'],
        'active_load' : round(active_watts, 0),
        'co2_kg'      : co2_kg,
        'sum_kwh'     : round(meter_state['sum_kwh'], 4),
        'uptime_s'    : int(time.time() - meter_state['start_time']),
    }

# ════════════════════════════════════════════
#  DATA LOGGING
# ════════════════════════════════════════════
FIELDS = ['timestamp','date','time','voltage','current','power',
          'power_factor','wh','kwh','sum_wh','sum_rupees','active_load']

def log_reading(data):
    exists = os.path.isfile(DATA_FILE)
    with open(DATA_FILE, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not exists: w.writeheader()
        w.writerow({k: data[k] for k in FIELDS})

def read_logs(n=200):
    if not os.path.exists(DATA_FILE): return []
    with open(DATA_FILE, 'r') as f:
        return list(csv.DictReader(f))[-n:]

def get_stats(rows):
    if not rows: return {}
    powers  = [float(r['power'])   for r in rows]
    volts   = [float(r['voltage']) for r in rows]
    currs   = [float(r['current']) for r in rows]
    return {
        'count'      : len(rows),
        'avg_power'  : round(sum(powers)/len(powers), 2),
        'max_power'  : round(max(powers), 2),
        'min_power'  : round(min(powers), 2),
        'avg_voltage': round(sum(volts)/len(volts), 2),
        'avg_current': round(sum(currs)/len(currs), 3),
        'first'      : rows[0]['timestamp'],
        'last'       : rows[-1]['timestamp'],
    }

# ════════════════════════════════════════════
#  GSM BILLING SIMULATION
#  Mirrors sendBilling() from .c file
# ════════════════════════════════════════════
def send_gsm_bill(mobile='9XXXXXXXXX'):
    msg = (f"Dear Customer, Your Energy Consumption is: "
           f"{meter_state['sum_wh']:.3f} WH "
           f"and Total Billing is Rs. {meter_state['sum_rupees']:.2f}")
    entry = {
        'time'  : datetime.datetime.now().strftime('%H:%M:%S'),
        'mobile': mobile,
        'msg'   : msg
    }
    meter_state['gsm_log'].insert(0, entry)
    meter_state['last_bill_time'] = entry['time']
    return entry

# ════════════════════════════════════════════
#  SEED HISTORICAL DATA
# ════════════════════════════════════════════
def seed_data():
    if os.path.exists(DATA_FILE) and os.path.getsize(DATA_FILE) > 200:
        # Re-load accumulated totals from file
        rows = read_logs(5000)
        if rows:
            meter_state['sum_wh']     = float(rows[-1]['sum_wh'])
            meter_state['sum_rupees'] = float(rows[-1]['sum_rupees'])
            meter_state['sum_kwh']    = float(rows[-1]['kwh'])
        return
    # Generate 24 hours of sample data
    base = datetime.datetime.now() - datetime.timedelta(hours=24)
    sum_wh = 0; sum_rs = 0
    for i in range(288):
        t    = base + datetime.timedelta(minutes=i * 5)
        h    = t.hour + t.minute / 60
        vrms = round(220 + 8 * math.sin(h * math.pi / 12) + random.uniform(-2, 2), 2)
        load = 1200 + 800 * math.sin((h - 6) * math.pi / 10) + random.uniform(-100, 100)
        load = max(200, load)
        irms = round(load / vrms + random.uniform(-0.05, 0.05), 3)
        pwr  = round(vrms * irms * POWER_FACTOR, 2)
        wh   = round(pwr / 3600, 5)
        sum_wh += wh
        rupees = wh * (3.15 if sum_wh<=50 else 3.60 if sum_wh<=100 else 4.25 if sum_wh<=250 else 5.20)
        sum_rs += rupees
        row = {
            'timestamp': t.strftime('%Y-%m-%d %H:%M:%S'),
            'date': t.strftime('%Y-%m-%d'), 'time': t.strftime('%H:%M:%S'),
            'voltage': vrms, 'current': irms, 'power': pwr,
            'power_factor': round(POWER_FACTOR + random.uniform(-0.01,0.01), 3),
            'wh': wh, 'kwh': round(sum_wh/1000, 4),
            'sum_wh': round(sum_wh, 3), 'sum_rupees': round(sum_rs, 2),
            'active_load': round(load, 0)
        }
        exists = os.path.isfile(DATA_FILE)
        with open(DATA_FILE, 'a', newline='') as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if not exists: w.writeheader()
            w.writerow(row)
    meter_state['sum_wh']     = sum_wh
    meter_state['sum_rupees'] = sum_rs
    meter_state['sum_kwh']    = sum_wh / 1000

# ════════════════════════════════════════════
#  ROUTES
# ════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/api/current')
def api_current():
    data  = simulate_energy()
    log_reading(data)
    rows  = read_logs()
    stats = get_stats(rows)
    # Alerts
    alerts = []
    if data['voltage'] < 200:
        alerts.append({'type':'danger','msg':'⚡ LOW VOLTAGE: Below 200V — check supply!'})
    elif data['voltage'] > 235:
        alerts.append({'type':'warning','msg':'⚡ HIGH VOLTAGE: Exceeds 235V — surge risk!'})
    if data['power'] > 2000:
        alerts.append({'type':'danger','msg':f"🔥 HIGH LOAD: {data['power']}W — possible overload!"})
    if data['sum_kwh'] > 0.1:
        alerts.append({'type':'info','msg':f"📊 Consumption: {data['sum_kwh']:.3f} kWh — Rs.{data['sum_rupees']:.2f}"})
    data['appliances'] = meter_state['appliances']
    data['gsm_log']    = meter_state['gsm_log'][:5]
    data['total_logs'] = len(rows)
    return jsonify({'current':data,'stats':stats,'alerts':alerts})

@app.route('/api/history')
def api_history():
    rows = read_logs(60)
    return jsonify({
        'labels' : [r['time']    for r in rows],
        'power'  : [float(r['power'])   for r in rows],
        'voltage': [float(r['voltage']) for r in rows],
        'current': [float(r['current']) for r in rows],
        'kwh'    : [float(r['kwh'])     for r in rows],
    })

@app.route('/api/logs')
def api_logs():
    rows = read_logs(50)
    return jsonify({'logs': list(reversed(rows))})

@app.route('/api/appliance', methods=['POST'])
def api_appliance():
    name = request.json.get('name')
    if name in meter_state['appliances']:
        meter_state['appliances'][name]['on'] = not meter_state['appliances'][name]['on']
    return jsonify({'appliances': meter_state['appliances']})

@app.route('/api/send_bill', methods=['POST'])
def api_send_bill():
    mobile = request.json.get('mobile', '9XXXXXXXXX')
    entry  = send_gsm_bill(mobile)
    return jsonify({'status':'sent','entry':entry})

@app.route('/api/reset_meter', methods=['POST'])
def api_reset_meter():
    meter_state['sum_wh']     = 0.0
    meter_state['sum_rupees'] = 0.0
    meter_state['sum_kwh']    = 0.0
    meter_state['peak_power'] = 0.0
    meter_state['start_time'] = time.time()
    if os.path.exists(DATA_FILE): os.remove(DATA_FILE)
    return jsonify({'status':'reset'})

@app.route('/api/export')
def api_export():
    rows = read_logs(5000)
    out  = io.StringIO()
    if rows:
        w = csv.DictWriter(out, fieldnames=rows[0].keys())
        w.writeheader(); w.writerows(rows)
    return Response(out.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition':'attachment;filename=energy_log.csv'})

if __name__ == '__main__':
    seed_data()
    print("="*58)
    print("  SMART ENERGY METER SYSTEM")
    print("  Arduino + ACS712 + SIM900 GSM + LCD 16x2")
    print("="*58)
    print("  Open browser: http://127.0.0.1:5000")
    print("="*58)
    app.run(debug=True, host='0.0.0.0', port=5000)
