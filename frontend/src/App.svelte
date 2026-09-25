<script>
  let username = 'processor'
  let password = 'herb123456'
  let token = localStorage.getItem('herb_token') || ''
  let role = localStorage.getItem('herb_role') || ''
  let me = localStorage.getItem('herb_user') || ''
  let view = 'records'

  // 炮制记录
  let rows = []
  let herb = '白芍'
  let factory = '甲厂'
  let factoryFilter = ''
  let tempC = 110
  let minutes = 10
  let error = ''

  // 另开账号
  let newUser = ''
  let newPass = ''
  let newRole = 'writer'
  let userMsg = ''

  // 厂际调拨
  let tHerb = '甘草'
  let tSource = '甲厂'
  let tTarget = '乙厂'
  let pending = []
  let effective = []
  let events = []
  let transferMsg = ''

  const fmt = (s) => (s ? new Date(s).toLocaleString() : '')

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '请求失败')
    return data
  }

  async function enter() {
    error = ''
    try {
      const data = await api('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      token = data.access_token
      role = data.role
      me = data.username
      localStorage.setItem('herb_token', token)
      localStorage.setItem('herb_role', role)
      localStorage.setItem('herb_user', me)
      await load()
    } catch (err) {
      error = err.message
    }
  }

  async function load() {
    const q = factoryFilter.trim()
    rows = await api('/api/batches' + (q ? '?factory=' + encodeURIComponent(q) : ''))
  }

  async function save() {
    error = ''
    try {
      await api('/api/batches', {
        method: 'POST',
        body: JSON.stringify({
          herb,
          factory,
          steps: [{ name: '清炒', temp_c: Number(tempC), minutes: Number(minutes) }],
        }),
      })
      await load()
    } catch (err) {
      error = err.message
    }
  }

  async function createAccount() {
    userMsg = ''
    try {
      const data = await api('/api/users', {
        method: 'POST',
        body: JSON.stringify({ username: newUser, password: newPass, role: newRole }),
      })
      userMsg = `已开账号 ${data.username}（${data.role === 'writer' ? '炮制员' : '质检员'}）`
      newUser = ''
      newPass = ''
    } catch (err) {
      userMsg = err.message
    }
  }

  async function loadTransfers() {
    const [p, e, ev] = await Promise.all([
      api('/api/transfers?status=pending'),
      api('/api/transfers?status=effective'),
      api('/api/transfers/events'),
    ])
    pending = p
    effective = e
    events = ev
  }

  async function createTransfer() {
    transferMsg = ''
    try {
      await api('/api/transfers', {
        method: 'POST',
        body: JSON.stringify({ herb: tHerb, source_factory: tSource, target_factory: tTarget }),
      })
      transferMsg = '调拨单已建，待另一名炮制员确认'
      await loadTransfers()
    } catch (err) {
      transferMsg = err.message
    }
  }

  async function confirmTransfer(id) {
    transferMsg = ''
    try {
      await api(`/api/transfers/${id}/confirm`, { method: 'POST' })
      transferMsg = '已确认，调拨生效'
      await loadTransfers()
    } catch (err) {
      transferMsg = err.message
    }
  }

  function show(next) {
    view = next
    transferMsg = ''
    if (next === 'transfers') loadTransfers().catch((err) => (transferMsg = err.message))
    else load().catch((err) => (error = err.message))
  }

  function leave() {
    localStorage.clear()
    token = ''
    role = ''
    me = ''
  }

  if (token) load()
</script>

<main>
  <h1>饮片炮制记录台</h1>
  {#if !token}
    <p>炮制记录整包保存。清炒温度须在 80 到 150，时长须在 5 到 30 分钟。</p>
    <input bind:value={username} placeholder="账号" />
    <input type="password" bind:value={password} placeholder="密码" />
    <button on:click={enter}>登录</button>
    {#if error}<p class="err">{error}</p>{/if}
    <p>processor / herb123456 可写；checker / check123456 只读</p>
  {:else}
    <nav>
      <a href="#records" class:active={view === 'records'} on:click|preventDefault={() => show('records')}>炮制记录</a>
      <a href="#transfers" class:active={view === 'transfers'} on:click|preventDefault={() => show('transfers')}>厂际调拨</a>
      <span class="who">{me}（{role === 'writer' ? '炮制员' : '质检员'}）</span>
      <button on:click={leave}>退出</button>
    </nav>

    {#if view === 'records'}
      <section>
        <input bind:value={factoryFilter} placeholder="按厂名筛，如 甲厂" />
        <button on:click={load}>筛选</button>
        <button on:click={() => { factoryFilter = ''; load() }}>全部</button>
      </section>
      {#if role === 'writer'}
        <section>
          <input bind:value={herb} placeholder="饮片" />
          <input bind:value={factory} placeholder="厂名" />
          <input type="number" bind:value={tempC} />
          <input type="number" bind:value={minutes} />
          <button on:click={save}>写入清炒记录</button>
          {#if error}<p class="err">{error}</p>{/if}
        </section>
        <section>
          <h3>另开账号</h3>
          <input bind:value={newUser} placeholder="新账号名" />
          <input type="password" bind:value={newPass} placeholder="密码（至少6位）" />
          <select bind:value={newRole}>
            <option value="writer">炮制员</option>
            <option value="reader">质检员</option>
          </select>
          <button on:click={createAccount}>开账号</button>
          {#if userMsg}<p>{userMsg}</p>{/if}
        </section>
      {/if}
      <ul>
        {#each rows as row}
          <li>{row.herb} · {row.factory} · {row.verdict} · {row.reason} · 温度 {row.doc.steps[0].temp_c}</li>
        {/each}
      </ul>
    {:else}
      {#if role === 'writer'}
        <section>
          <h3>建调拨单</h3>
          <input bind:value={tSource} placeholder="源厂名" />
          <input bind:value={tTarget} placeholder="目标厂名" />
          <input bind:value={tHerb} placeholder="饮片名" />
          <button on:click={createTransfer}>建单</button>
        </section>
      {/if}
      {#if transferMsg}<p>{transferMsg}</p>{/if}

      <h3>待确认</h3>
      <ul>
        {#each pending as t}
          <li>
            {t.herb} · {t.source_factory} → {t.target_factory} · 建单 {t.created_by} · {fmt(t.created_at)}
            {#if role === 'writer'}
              {#if t.created_by === me}
                <span class="muted">需另一名炮制员确认</span>
              {:else}
                <button on:click={() => confirmTransfer(t.id)}>确认</button>
              {/if}
            {/if}
          </li>
        {:else}
          <li class="muted">暂无待确认调拨单</li>
        {/each}
      </ul>

      <h3>已生效</h3>
      <ul>
        {#each effective as t}
          <li>{t.herb} · {t.source_factory} → {t.target_factory} · 建单 {t.created_by} · 确认 {t.confirmed_by} · {fmt(t.confirmed_at)}</li>
        {:else}
          <li class="muted">暂无已生效调拨单</li>
        {/each}
      </ul>

      <h3>调拨流水</h3>
      <ul>
        {#each events as e}
          <li>{e.action} · {e.actor} · {e.detail} · 单号 {e.transfer_id} · {fmt(e.created_at)}</li>
        {:else}
          <li class="muted">暂无流水</li>
        {/each}
      </ul>
    {/if}
  {/if}
</main>

<style>
  main { font-family: sans-serif; max-width: 720px; margin: 24px auto; color: #3f2f1f; }
  h1 { color: #7c2d12; }
  input { margin-right: 8px; padding: 6px; }
  nav { display: flex; align-items: center; gap: 16px; border-bottom: 1px solid #d6c8b8; padding-bottom: 8px; margin-bottom: 16px; }
  nav a { color: #7c2d12; text-decoration: none; padding: 4px 8px; }
  nav a.active { font-weight: bold; border-bottom: 2px solid #7c2d12; }
  nav .who { margin-left: auto; color: #8a7360; }
  section { margin-bottom: 16px; }
  .err { color: #b91c1c; }
  .muted { color: #8a7360; }
</style>
