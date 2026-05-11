"""
可视化配置向导 — 通过浏览器管理 API Key 和系统设置
用法: python setup_wizard.py
自动打开: http://localhost:8899
"""
import http.server
import json
import os
import configparser
import webbrowser
import urllib.parse
import requests

PORT = 8899
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>Alert System — Setup Wizard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0f1117;--card:#1a1d2e;--border:#2a2d3e;--text:#e0e0e0;--muted:#888;
--primary:#5c6bc0;--success:#4caf50;--warn:#ff9800;--danger:#ef5350;--input-bg:#252838}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:var(--bg);color:var(--text);min-height:100vh}
.container{max-width:900px;margin:0 auto;padding:30px 20px}
h1{font-size:28px;margin-bottom:8px;background:linear-gradient(135deg,#5c6bc0,#7986cb);
-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.subtitle{color:var(--muted);margin-bottom:30px;font-size:14px}
.tabs{display:flex;gap:4px;margin-bottom:24px;border-bottom:1px solid var(--border);padding-bottom:0}
.tab{padding:10px 20px;cursor:pointer;border-radius:8px 8px 0 0;color:var(--muted);font-size:14px;
transition:all .2s;border:1px solid transparent;border-bottom:none}
.tab:hover{color:var(--text);background:var(--card)}
.tab.active{color:#fff;background:var(--card);border-color:var(--border)}
.panel{display:none;animation:fadeIn .3s}
.panel.active{display:block}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:24px;margin-bottom:20px}
.card-title{font-size:16px;font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.field{margin-bottom:16px}
.field label{display:block;font-size:13px;color:var(--muted);margin-bottom:6px}
.field input,.field select{width:100%;padding:10px 14px;background:var(--input-bg);border:1px solid var(--border);
border-radius:8px;color:var(--text);font-size:14px;outline:none;transition:border-color .2s}
.field input:focus{border-color:var(--primary)}
.field input.ok{border-color:var(--success)}
.field input.missing{border-color:var(--warn)}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.btn{padding:10px 24px;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:500;transition:all .2s}
.btn-primary{background:var(--primary);color:#fff}
.btn-primary:hover{background:#3f51b5}
.btn-sm{padding:6px 14px;font-size:12px;border-radius:6px}
.btn-outline{background:transparent;border:1px solid var(--border);color:var(--text)}
.btn-outline:hover{border-color:var(--primary);color:var(--primary)}
.status{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.status.on{background:var(--success)}.status.off{background:var(--danger)}
.toast{position:fixed;bottom:30px;right:30px;background:var(--success);color:#fff;padding:14px 24px;
border-radius:10px;font-size:14px;opacity:0;transition:opacity .3s;pointer-events:none;z-index:9999}
.toast.show{opacity:1}
.domain-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.domain-chip{background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:14px}
.domain-chip h4{font-size:14px;margin-bottom:6px;color:var(--primary)}
.domain-chip .kw{font-size:12px;color:var(--muted);line-height:1.6}
.footer{text-align:center;color:var(--muted);font-size:12px;margin-top:40px;padding-top:20px;border-top:1px solid var(--border)}
.angles-list{display:grid;gap:12px}
.angle-card{background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:16px}
.angle-card h4{color:var(--primary);margin-bottom:4px}.angle-card p{font-size:13px;color:var(--muted)}
.angle-card .outline{font-size:12px;color:var(--text);margin-top:8px;padding-left:16px}
</style>
</head>
<body>
<div class="container">
<h1>⚡ Alert System — Setup Wizard</h1>
<p class="subtitle" id="brandLine">可视化配置管理 · 支持一键保存</p>
<div class="tabs">
<div class="tab active" onclick="switchTab('keys')">🔑 API Keys</div>
<div class="tab" onclick="switchTab('email')">📧 邮件设置</div>
<div class="tab" onclick="switchTab('domain')">🎯 领域概览</div>
<div class="tab" onclick="switchTab('angles')">📐 内容角度</div>
<div class="tab" onclick="switchTab('health')">💚 运行状态</div>
</div>

<div id="panel-keys" class="panel active">
<div class="card">
<div class="card-title">🤖 AI 模型 API (云雾 yunwu.ai)</div>
<div class="field"><label>Base URL</label><input id="api-base_url" placeholder="https://yunwu.ai/v1/chat/completions"></div>
<div class="row">
<div class="field"><label>Cheap Key (日常)</label><input id="api-api_key_cheap" type="password" placeholder="sk-..."></div>
<div class="field"><label>Premium Key (备用)</label><input id="api-api_key_premium" type="password" placeholder="sk-..."></div>
</div>
<div class="row">
<div class="field"><label>主模型</label><input id="api-api_model" placeholder="gemini-3.1-flash-lite-preview"></div>
<div class="field"><label>备用模型</label><input id="api-api_model_backup" placeholder="gpt-5.2"></div>
</div>
<div class="field"><label>Jina Search Key</label><input id="api-jina_api_key" placeholder="jina_..."></div>
</div>

<div class="card">
<div class="card-title">📡 搜索信息源 API</div>
<div id="sources-fields"></div>
</div>

<button class="btn btn-primary" onclick="saveConfig()">💾 保存所有配置</button>
</div>

<div id="panel-email" class="panel">
<div class="card">
<div class="card-title">📧 SMTP 邮件设置</div>
<div class="row">
<div class="field"><label>SMTP Host</label><input id="email-email_host" placeholder="smtp.exmail.qq.com"></div>
<div class="field"><label>Port</label><input id="email-email_port" type="number" placeholder="465"></div>
</div>
<div class="row">
<div class="field"><label>发件人</label><input id="email-email_sender" placeholder="user@example.com"></div>
<div class="field"><label>密码/授权码</label><input id="email-email_password" type="password"></div>
</div>
<div class="field"><label>收件人 (多个用逗号分隔)</label><input id="email-email_receiver" placeholder="team@example.com"></div>
</div>
<button class="btn btn-primary" onclick="saveConfig()">💾 保存邮件配置</button>
</div>

<div id="panel-domain" class="panel">
<div class="card">
<div class="card-title">🎯 当前领域: <span id="domain-name" style="color:var(--primary)"></span></div>
<p style="color:var(--muted);font-size:13px;margin-bottom:16px" id="domain-desc"></p>
<div class="domain-grid" id="domain-grid"></div>
</div>
<p style="color:var(--muted);font-size:13px;margin-top:12px">💡 修改关键词矩阵请直接编辑 <code>domain_config.py</code> 文件</p>
</div>

<div id="panel-angles" class="panel">
<div class="card">
<div class="card-title">📐 6 大选题视角</div>
<div class="angles-list" id="angles-list"></div>
</div>
<p style="color:var(--muted);font-size:13px;margin-top:12px">💡 修改视角定义请直接编辑 <code>domain_config.py</code> → content_angles</p>
</div>

<div id="panel-health" class="panel">
<div class="card">
<div class="card-title">💚 信息源健康状态</div>
<div id="health-sources"></div>
</div>
<div class="card">
<div class="card-title">📋 最近日志 (最后 30 行)</div>
<pre id="health-logs" style="background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:16px;font-size:12px;max-height:400px;overflow-y:auto;color:var(--muted);white-space:pre-wrap;word-break:break-all"></pre>
</div>
</div>
</div>

<div class="footer">Alert System Setup Wizard · 配置保存至 config.ini</div>
</div>
<div class="toast" id="toast"></div>

<script>
const SOURCES_META = [
  {key:'tavily_api_key', name:'Tavily', desc:'深度搜索 (1000次/月)', limit:'tavily_daily_limit'},
  {key:'exa_api_key', name:'Exa.ai', desc:'神经搜索/研报 (1000次/月)', limit:'exa_daily_limit'},
  {key:'newsapi_key', name:'NewsAPI', desc:'全球实时新闻 (100次/天)', limit:'newsapi_daily_limit'},
  {key:'gnews_api_key', name:'GNews', desc:'中文新闻 (100次/天)', limit:'gnews_daily_limit'},
  {key:'google_cx_id', name:'Google CX ID', desc:'自定义搜索引擎 ID', limit:null},
  {key:'google_cx_api_key', name:'Google CX Key', desc:'自定义搜索 API Key (100次/天)', limit:'google_cx_daily_limit'},
  {key:'brave_api_key', name:'Brave Search', desc:'独立索引搜索+新闻 (1000次/月)', limit:'brave_daily_limit'},
];

function escapeHtml(s){if(!s)return'';return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

function switchTab(id){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('panel-'+id).classList.add('active');
}

function toast(msg){
  const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),2500);
}

function renderSourceFields(){
  const c=document.getElementById('sources-fields');
  SOURCES_META.forEach(s=>{
    let h=`<div class="row"><div class="field"><label><span class="status off" id="st-${s.key}"></span>${s.name} — ${s.desc}</label><div style="display:flex;gap:8px"><input id="sources-${s.key}" placeholder="粘贴 API Key..." style="flex:1"><button class="btn btn-sm btn-outline" onclick="testSource('${s.key}','${s.name}')" id="tb-${s.key}">测试</button></div></div>`;
    if(s.limit) h+=`<div class="field"><label>每日上限</label><input id="sources-${s.limit}" type="number" style="max-width:120px"></div>`;
    h+=`</div>`;
    c.innerHTML+=h;
  });
}

async function loadConfig(){
  try{
    const r=await fetch('/api/config');const d=await r.json();
    // API section
    if(d.API) Object.entries(d.API).forEach(([k,v])=>{const el=document.getElementById('api-'+k);if(el)el.value=v||''});
    // SOURCES section
    if(d.SOURCES) Object.entries(d.SOURCES).forEach(([k,v])=>{
      const el=document.getElementById('sources-'+k);if(el){el.value=v||'';
        const st=document.getElementById('st-'+k);
        if(st){if(v&&!v.startsWith('YOUR_'))st.className='status on';else st.className='status off';}
      }
    });
    // EMAIL section
    if(d.EMAIL_AI) Object.entries(d.EMAIL_AI).forEach(([k,v])=>{const el=document.getElementById('email-'+k);if(el)el.value=v||''});
    // Domain info
    if(d._domain){
      document.getElementById('domain-name').textContent=d._domain.brand||'';
      document.getElementById('domain-desc').textContent=d._domain.description||'';
      document.getElementById('brandLine').textContent=`${d._domain.brand_emoji||''} ${d._domain.brand||''} · 可视化配置管理`;
      // Keyword grid
      const grid=document.getElementById('domain-grid');grid.innerHTML='';
      if(d._domain.keyword_matrix){
        Object.entries(d._domain.keyword_matrix).forEach(([cat,kw])=>{
          const coreStr=(kw.core||[]).join(', ');
          const entStr=(kw.entities||[]).join(', ');
          grid.innerHTML+=`<div class="domain-chip"><h4>${escapeHtml(cat)}</h4><div class="kw"><b>核心词:</b> ${escapeHtml(coreStr)}</div><div class="kw"><b>实体:</b> ${escapeHtml(entStr)}</div></div>`;
        });
      }
      // Angles
      const al=document.getElementById('angles-list');al.innerHTML='';
      if(d._domain.content_angles){
        Object.entries(d._domain.content_angles).forEach(([id,a])=>{
          const ol=(a.outline_template||[]).map((o,i)=>`<li>${escapeHtml(o)}</li>`).join('');
          al.innerHTML+=`<div class="angle-card"><h4>${escapeHtml(a.name)}</h4><p>${escapeHtml(a.description)}</p><p style="margin-top:4px;font-size:12px;color:var(--warn)">触发: ${escapeHtml(a.trigger)}</p><ol class="outline">${ol}</ol></div>`;
        });
      }
    }
  }catch(e){console.error(e)}
}

async function saveConfig(){
  const data={API:{},SOURCES:{},EMAIL_AI:{},APP_SETTINGS:{}};
  document.querySelectorAll('[id^="api-"]').forEach(el=>{data.API[el.id.replace('api-','')]=el.value});
  document.querySelectorAll('[id^="sources-"]').forEach(el=>{data.SOURCES[el.id.replace('sources-','')]=el.value});
  document.querySelectorAll('[id^="email-"]').forEach(el=>{data.EMAIL_AI[el.id.replace('email-','')]=el.value});
  try{
    const r=await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const res=await r.json();
    if(res.ok){toast('✅ 配置已保存');loadConfig();}else toast('❌ 保存失败');
  }catch(e){toast('❌ 网络错误')}
}

async function testSource(key,name){
  const btn=document.getElementById('tb-'+key);
  btn.textContent='测试中...';btn.disabled=true;
  try{
    const r=await fetch('/api/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:key})});
    const d=await r.json();
    if(d.ok){toast(`✅ ${name} 连接正常`);btn.textContent='✓ 正常';btn.style.color='var(--success)';}
    else{toast(`❌ ${name} 连接失败: ${d.error||'未知错误'}`);btn.textContent='✗ 失败';btn.style.color='var(--danger)';}
  }catch(e){toast(`❌ ${name} 测试异常`);btn.textContent='✗ 异常';btn.style.color='var(--danger)';}
  setTimeout(()=>{btn.textContent='测试';btn.disabled=false;btn.style.color='';},3000);
}

async function loadHealth(){
  try{
    const r=await fetch('/api/health');const d=await r.json();
    // Sources health
    const sc=document.getElementById('health-sources');sc.innerHTML='';
    if(d.sources&&d.sources.length){
      d.sources.forEach(s=>{
        const color=s.disabled?'var(--danger)':(s.failures>0?'var(--warn)':'var(--success)');
        const status=s.disabled?'已禁用':(s.failures>0?`连续失败 ${s.failures} 次`:'正常');
        sc.innerHTML+=`<div style="display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--border)"><span class="status" style="background:${color}"></span><span style="flex:1;font-weight:500">${s.name}</span><span style="color:${color};font-size:13px">${status}</span></div>`;
      });
    }else{sc.innerHTML='<p style="color:var(--muted)">暂无健康数据（运行一次后生成）</p>';}
    // Logs
    const lg=document.getElementById('health-logs');
    lg.textContent=d.logs||'暂无日志';
    lg.scrollTop=lg.scrollHeight;
  }catch(e){console.error(e)}
}

renderSourceFields();
loadConfig();
loadHealth();
</script>
</body>
</html>"""


class WizardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def do_GET(self):
        if self.path == '/':
            self._send(200, 'text/html', HTML_PAGE.encode('utf-8'))
        elif self.path == '/api/config':
            self._send_json(self._read_config())
        elif self.path == '/api/health':
            self._send_json(self._read_health())
        else:
            self._send(404, 'text/plain', b'Not Found')

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 1_000_000:
            self._send(413, 'text/plain', b'Request body too large')
            return
        if self.path == '/api/save':
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode('utf-8'))
                self._save_config(data)
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
        elif self.path == '/api/test':
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode('utf-8'))
                result = self._test_source(data.get('source', ''))
                self._send_json(result)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self._send(200, 'application/json', body)

    def _read_config(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read(CONFIG_PATH, encoding='utf-8')
        result = {}
        for section in config.sections():
            result[section] = dict(config[section])

        # Inject domain info (read-only)
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("domain_config",
                os.path.join(os.path.dirname(CONFIG_PATH), "domain_config.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            d = mod.DOMAIN
            result['_domain'] = {
                'name': d.get('name'), 'brand': d.get('brand'),
                'brand_emoji': d.get('brand_emoji'), 'description': d.get('description'),
                'keyword_matrix': d.get('keyword_matrix', {}),
                'content_angles': d.get('content_angles', {}),
                'categories': d.get('categories', []),
            }
        except Exception: pass
        return result

    def _test_source(self, source_key):
        """测试指定 API 源的连接"""
        import requests
        config = configparser.ConfigParser(interpolation=None)
        config.read(CONFIG_PATH, encoding='utf-8')

        # AI 模型测试
        if source_key in ('api_key_cheap', 'api_key_premium'):
            key = config.get('API', source_key, fallback='')
            model = config.get('API', 'api_model', fallback='')
            base_url = config.get('API', 'base_url', fallback='')
            if not key or not model or not base_url:
                return {"ok": False, "error": "配置不完整"}
            try:
                headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                payload = {"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}
                r = requests.post(base_url, headers=headers, json=payload, timeout=10)
                return {"ok": r.status_code == 200, "error": f"HTTP {r.status_code}" if r.status_code != 200 else None}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        # 搜索源测试
        key = config.get('SOURCES', source_key, fallback='')
        if not key or key.startswith('YOUR_'):
            return {"ok": False, "error": "未配置或为占位符"}

        test_url = None
        test_headers = {}
        if source_key == 'tavily_api_key':
            test_url = "https://api.tavily.com/search"
            test_headers = {"Content-Type": "application/json"}
            try:
                r = requests.post(test_url, headers=test_headers, json={"api_key": key, "query": "test", "max_results": 1}, timeout=10)
                return {"ok": r.status_code == 200, "error": f"HTTP {r.status_code}" if r.status_code != 200 else None}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        elif source_key == 'exa_api_key':
            test_url = "https://api.exa.ai/search"
            test_headers = {"x-api-key": key, "Content-Type": "application/json"}
            try:
                r = requests.post(test_url, headers=test_headers, json={"query": "test", "numResults": 1}, timeout=10)
                return {"ok": r.status_code == 200, "error": f"HTTP {r.status_code}" if r.status_code != 200 else None}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        elif source_key == 'newsapi_key':
            test_url = f"https://newsapi.org/v2/top-headlines?country=us&pageSize=1"
            test_headers = {"X-Api-Key": key}
            try:
                r = requests.get(test_url, headers=test_headers, timeout=10)
                return {"ok": r.status_code == 200, "error": f"HTTP {r.status_code}" if r.status_code != 200 else None}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        elif source_key == 'brave_api_key':
            test_url = "https://api.search.brave.com/res/v1/web/search?q=test&count=1"
            test_headers = {"X-Subscription-Token": key, "Accept": "application/json"}
            try:
                r = requests.get(test_url, headers=test_headers, timeout=10)
                return {"ok": r.status_code == 200, "error": f"HTTP {r.status_code}" if r.status_code != 200 else None}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        else:
            return {"ok": False, "error": "该源暂不支持连接测试"}

    def _read_health(self):
        """读取健康状态和最近日志"""
        result = {"sources": [], "logs": ""}

        # 读取 source_health.json
        health_file = os.path.join(os.path.dirname(CONFIG_PATH), 'source_health.json')
        if os.path.exists(health_file):
            try:
                with open(health_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for name, info in data.items():
                    result["sources"].append({
                        "name": name,
                        "failures": info.get("consecutive_failures", 0),
                        "disabled": info.get("disabled", False),
                        "date": info.get("date", ""),
                    })
            except Exception:
                pass

        # 读取最近日志
        log_file = os.path.join(os.path.dirname(CONFIG_PATH), 'run_log.txt')
        if os.path.exists(log_file):
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                result["logs"] = "".join(lines[-30:])
            except Exception:
                pass

        return result

    def _save_config(self, data):
        config = configparser.ConfigParser(interpolation=None)
        config.read(CONFIG_PATH, encoding='utf-8')

        for section, values in data.items():
            if section.startswith('_'): continue
            if section not in config: config[section] = {}
            for k, v in values.items():
                if v is not None and str(v).strip():
                    config[section][k] = str(v)

        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            config.write(f)


if __name__ == '__main__':
    print(f"\n{'='*50}")
    print(f"  ⚡ Alert System — Setup Wizard")
    print(f"  浏览器打开: http://localhost:{PORT}")
    print(f"{'='*50}\n")
    webbrowser.open(f'http://localhost:{PORT}')
    server = http.server.HTTPServer(('127.0.0.1', PORT), WizardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n向导已关闭。")
