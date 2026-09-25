<script>
  let username = 'processor'
  let password = 'herb123456'
  let token = localStorage.getItem('herb_token') || ''
  let role = localStorage.getItem('herb_role') || ''
  let currentName = localStorage.getItem('herb_username') || ''
  let view = location.hash
  let error = ''

  // 炮制总表
  let rows = []
  let herb = '白芍'
  let plant = '甲厂'
  let tempC = 110
  let minutes = 10
  let plantFilter = '甲厂'

  // 厂际调拨
  let transfers = []
  let logs = []
  let sourcePlant = '甲厂'
  let targetPlant = '乙厂'
  let transferHerb = '甘草'
  let transferError = ''

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
      currentName = data.username
      localStorage.setItem('herb_token', token)
      localStorage.setItem('herb_role', role)
      localStorage.setItem('herb_username', currentName)
      await loadView()
    } catch (err) {
      error = err.message
    }
  }

  async function loadBatches() {
    // 厂名筛选条件交给服务端算，前端不传过滤后的结果
    const query = plantFilter ? `?plant=${encodeURIComponent(plantFilter)}` : ''
    rows = await api(`/api/batches${query}`)
  }

  async function loadTransfers() {
    const data = await api('/api/transfers')
    transfers = data.transfers
    logs = data.logs
  }

  async function loadView() {
    if (view === '#/transfers') {
      await loadTransfers()
    } else {
      await loadBatches()
    }
  }

  async function save() {
    error = ''
    try {
      await api('/api/batches', {
        method: 'POST',
        body: JSON.stringify({
          herb,
          plant,
          steps: [{ name: '清炒', temp_c: Number(tempC), minutes: Number(minutes) }],
        }),
      })
      await loadBatches()
    } catch (err) {
      error = err.message
    }
  }

  async function createTransfer() {
    transferError = ''
    try {
      await api('/api/transfers', {
        method: 'POST',
        body: JSON.stringify({
          source_plant: sourcePlant,
          target_plant: targetPlant,
          herb: transferHerb,
        }),
      })
      await loadTransfers()
    } catch (err) {
      transferError = err.message
    }
  }

  async function confirmTransfer(id) {
    transferError = ''
    try {
      await api(`/api/transfers/${id}/confirm`, { method: 'POST' })
      await loadTransfers()
    } catch (err) {
      transferError = err.message
    }
  }

  async function syncHash() {
    view = location.hash || '#/'
    if (token) await loadView()
  }

  function go(hash) {
    location.hash = hash
  }

  function leave() {
    localStorage.clear()
    token = ''
    role = ''
    currentName = ''
  }

  $: pendingTransfers = transfers.filter((t) => t.status === 'pending')
  $: effectiveTransfers = transfers.filter((t) => t.status === 'effective')

  if (typeof window !== 'undefined') {
    window.addEventListener('hashchange', syncHash)
  }
  if (token) loadView()
</script>

<main>
  <h1>饮片炮制记录台</h1>
  {#if !token}
    <p>炮制记录整包保存。清炒温度须在 80 到 150，时长须在 5 到 30 分钟。</p>
    <input bind:value={username} />
    <input type="password" bind:value={password} />
    <button on:click={enter}>登录</button>
    {#if error}<p class="error">{error}</p>{/if}
    <p>processor / herb123456 可写；processor2 / herb123456 第二名炮制员（双岗确认用）；checker / check123456 只读</p>
  {:else}
    <nav class="topbar">
      <a href="#/" class:active={view !== '#/transfers'}>炮制总表</a>
      <a href="#/transfers" class:active={view === '#/transfers'}>厂际调拨</a>
      <span class="spacer"></span>
      <span>{currentName}（{role === 'writer' ? '炮制员' : '质检员'}）</span>
      <button on:click={leave}>退出</button>
    </nav>

    {#if view !== '#/transfers'}
      <section>
        {#if role === 'writer'}
          <div class="formline">
            <input bind:value={herb} placeholder="饮片" />
            <input bind:value={plant} placeholder="厂名" />
            <input type="number" bind:value={tempC} />
            <input type="number" bind:value={minutes} />
            <button on:click={save}>写入清炒记录</button>
          </div>
          {#if error}<p class="error">{error}</p>{/if}
        {/if}
        <div class="formline">
          <label>按厂名筛（服务端计算）：</label>
          <select bind:value={plantFilter} on:change={loadBatches}>
            <option value="">全部厂</option>
            <option value="甲厂">甲厂</option>
            <option value="乙厂">乙厂</option>
          </select>
        </div>
        <ul>
          {#each rows as row}
            <li>{row.herb} · {row.verdict} · {row.reason} · 温度 {row.doc.steps[0].temp_c} · 当前在 {row.current_plant}</li>
          {/each}
        </ul>
      </section>
    {:else}
      <section>
        <h2>待确认</h2>
        {#if role === 'writer'}
          <div class="formline">
            <input bind:value={sourcePlant} placeholder="源厂名" />
            <input bind:value={targetPlant} placeholder="目标厂名" />
            <input bind:value={transferHerb} placeholder="饮片名" />
            <button on:click={createTransfer}>建调拨单</button>
          </div>
          {#if transferError}<p class="error">{transferError}</p>{/if}
        {:else}
          <p class="hint">质检员只能翻看，不能建单、不能确认。</p>
        {/if}
        <ul>
          {#each pendingTransfers as t}
            <li>
              #{t.id} {t.source_plant} → {t.target_plant} · {t.herb} · 建单人 {t.created_by}
              {#if role === 'writer'}
                {#if t.created_by === currentName}
                  <button disabled title="建单人不能自行确认">待他人确认</button>
                {:else}
                  <button on:click={() => confirmTransfer(t.id)}>确认生效</button>
                {/if}
              {/if}
            </li>
          {/each}
        </ul>

        <h2>已生效</h2>
        <ul>
          {#each effectiveTransfers as t}
            <li>#{t.id} {t.source_plant} → {t.target_plant} · {t.herb} · {t.created_by} 建单 · {t.confirmed_by} 确认</li>
          {/each}
        </ul>

        <h2>调拨流水</h2>
        <ul>
          {#each logs as log}
            <li>#{log.transfer_id} {log.action === 'created' ? '建单' : '确认'} · {log.actor} · {log.detail}</li>
          {/each}
        </ul>
      </section>
    {/if}
  {/if}
</main>

<style>
  main { font-family: sans-serif; max-width: 860px; margin: 24px auto; color: #3f2f1f; }
  h1 { color: #7c2d12; }
  h2 { color: #7c2d12; margin-top: 24px; }
  input, select { margin-right: 8px; padding: 6px; }
  .topbar { display: flex; align-items: center; gap: 16px; border-bottom: 2px solid #7c2d12; padding: 8px 0; margin-bottom: 16px; }
  .topbar a { text-decoration: none; color: #7c2d12; font-weight: bold; }
  .topbar a.active { text-decoration: underline; }
  .spacer { flex: 1; }
  .formline { margin: 8px 0; display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
  .error { color: #b91c1c; }
  .hint { color: #6b6b6b; }
</style>
