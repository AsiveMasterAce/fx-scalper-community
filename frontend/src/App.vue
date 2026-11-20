<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import CandleChart from './components/CandleChart.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import JournalTable from './components/JournalTable.vue'
import SymbolPicker from './components/SymbolPicker.vue'
import ToastStack from './components/ToastStack.vue'
import { formatSastDateTime, formatSastTime } from './time.js'
import axios from 'axios'

const baseUrl = import.meta.env.BASE_URL || '/'
const utcHourToSast = (hour) => (Number(hour || 0) + 2) % 24
const sastHourToUtc = (hour) => (Number(hour || 0) + 22) % 24
const apiBaseUrl = (
  import.meta.env.VITE_API_BASE_URL ||
  `${baseUrl.replace(/\/+$/, '') || ''}/api/`
)
  .replace(/\/+$/, '/')

const api = axios.create({
  baseURL: apiBaseUrl,
})

const API_KEY_STORAGE = 'fx-scalper-community-api-key'

api.interceptors.request.use((config) => {
  const key = window.localStorage.getItem(API_KEY_STORAGE)
  if (key) {
    config.headers['X-API-Key'] = key
  }
  return config
})

api.interceptors.response.use(undefined, (error) => {
  const config = error?.config
  if (error?.response?.status !== 401 || !config || config._apiKeyRetried) {
    return Promise.reject(error)
  }
  const key = window.prompt('This backend requires an API key (COMMUNITY_API_KEY). Enter it:')
  if (!key) {
    return Promise.reject(error)
  }
  window.localStorage.setItem(API_KEY_STORAGE, key.trim())
  config._apiKeyRetried = true
  return api.request(config)
})

const loading = ref(true)
const busy = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const bootstrapData = ref({
  settings: {},
  market: [],
  signals: [],
  portfolio: [],
  trade_history: [],
  rules: [],
  rebalance_plan: { actions: [], current_weights: {}, target_weights: {} },
  last_backtest: null,
  last_broker_status: null,
  broker_sync: null,
  last_scan: null,
  symbols: [],
  stats: {},
  setup: {},
})

const settingsForm = reactive({
  trade_direction: 'both',
  daily_profit_target: 0,
  daily_loss_limit: 0,
  auto_scan_interval_seconds: 300,
  auto_hold_minutes: 25,
  auto_paper_exposure: 100,
  paper_starting_balance: 10000,
  active_exit_enabled: false,
  early_take_profit_pct: 50,
  early_stop_loss_pct: 50,
  spike_grab_enabled: true,
  spike_grab_min_profit_pct: 25,
  trailing_profit_enabled: true,
  trailing_arm_pct: 30,
  trailing_pullback_pct: 12,
  reversal_exit_enabled: true,
  max_positions_per_symbol: 1,
  scale_in_min_profit_pct: 20,
  scale_in_cooldown_minutes: 5,
  guardian_check_interval_seconds: 10,
  stop_and_reverse_enabled: false,
  max_flips_per_day: 3,
  dynamic_stake_enabled: false,
  dynamic_stake_pct: 2,
  dynamic_stake_min: 0.5,
  dynamic_stake_max: 5,
  anchor_scalp_enabled: false,
  scalp_duration: 30,
  scalp_duration_unit: 'm',
  scalp_take_profit_pct: 30,
  mtf_confirm_enabled: false,
  mtf_granularity: 900,
  auto_clamp_duration_enabled: false,
  market_close_hour_utc: 21,
  deriv_contract_mode: 'rise_fall',
  deriv_multiplier: 100,
  max_trades_per_day: 0,
  max_loss_streak: 0,
  loss_cooldown_minutes: 0,
  trading_hours_start: 0,
  trading_hours_end: 0,
  paper_trading_enabled: true,
  live_trading_enabled: false,
  broker_provider: 'deriv',
  deriv_app_id: '',
  deriv_token: '',
  market_source: 'deriv',
  binance_data_url: 'https://data-api.binance.vision',
  binance_api_key: '',
  binance_api_secret: '',
  binance_testnet: true,
  deriv_api_url: 'https://api.derivws.com',
  deriv_ws_url: 'wss://ws.derivws.com/websockets/v3',
  deriv_options_account_mode: 'demo',
  deriv_options_account_id: '',
  risk_profile: 'balanced',
  max_open_positions: 6,
  live_trade_stake: 1,
  live_trade_currency: 'USD',
  live_trade_duration: 5,
  live_trade_duration_unit: 't',
  rebalance_enabled: true,
  rebalance_key_mode: 'asset_class',
  rebalance_tolerance_pct: 8,
})

const tradeForm = reactive({
  symbol: '',
  side: 'BUY',
  exposure: 250,
  price: 0,
})

const liveTradeForm = reactive({
  symbol: '',
  side: 'BUY',
  amount: 1,
  currency: 'USD',
  duration: 5,
  duration_unit: 't',
})

const allocationJson = ref('{}')
const symbolsJson = ref('[]')
const marketSnapshotsJson = ref('[]')
const liveOrderResult = ref(null)

const TABS = [
  { id: 'dashboard', label: 'Home', icon: '⌂' },
  { id: 'signals', label: 'Signals & trades', icon: '↗' },
  { id: 'trade', label: 'Practice trade', icon: '▶' },
  { id: 'journal', label: 'My activity', icon: '☷' },
  { id: 'backtest', label: 'Test strategy', icon: '✓' },
  { id: 'settings', label: 'Setup', icon: '⚙' },
]
const TAB_HELP = {
  dashboard: { title: 'Your starting point', text: 'See what the app is doing and whether anything needs your attention.', tip: 'New here? Use sample data, then visit Practice trade. No real money is used unless you deliberately enable live trading.' },
  signals: { title: 'Suggestions and trades', text: 'Review current signals and recent trade activity without the rest of the dashboard.', tip: 'Use View when you want the full entry, stop-loss, take-profit, and cancellation guidance.' },
  trade: { title: 'Practice without risking money', text: 'Try opening and closing a pretend trade so you can learn how the process works.', tip: 'A “buy” expects the price to rise. A “sell” expects it to fall. The amount at risk is shown before you continue.' },
  journal: { title: 'Review what happened', text: 'See your previous practice and live trades in one place.', tip: 'Look for patterns over many trades. One win or loss does not tell you whether a strategy is reliable.' },
  backtest: { title: 'Try the strategy on past data', text: 'Run a safe simulation to see how the strategy might have behaved before today.', tip: 'Past results are useful for learning, but they cannot predict or guarantee future results.' },
  settings: { title: 'Choose how the app should work', text: 'Connect a data source, choose markets, and set safety limits.', tip: 'You can leave advanced options unchanged. The starter settings are designed for practice.' },
}
const initialTab = window.location.hash.slice(1)
const tab = ref(TABS.some((item) => item.id === initialTab) ? initialTab : 'dashboard')
const currentTabHelp = computed(() => TAB_HELP[tab.value])
const showPageHelp = ref(true)
const showAdvancedTrading = ref(false)
const expandedGuidance = ref(new Set())
function toggleGuidance(symbol) {
  const next = new Set(expandedGuidance.value)
  next.has(symbol) ? next.delete(symbol) : next.add(symbol)
  expandedGuidance.value = next
}
watch(tab, (value) => window.history.replaceState(null, '', `#${value}`))
window.addEventListener('hashchange', () => {
  const next = window.location.hash.slice(1)
  if (TABS.some((item) => item.id === next)) tab.value = next
})

const THEME_STORAGE = 'fx-scalper-community-theme-v2'
const theme = ref(localStorage.getItem(THEME_STORAGE) || 'light')
watch(
  theme,
  (value) => {
    document.documentElement.dataset.theme = value
    localStorage.setItem(THEME_STORAGE, value)
  },
  { immediate: true },
)

const toasts = ref([])
let toastId = 0
function dismissToast(id) {
  toasts.value = toasts.value.filter((toast) => toast.id !== id)
}
function pushToast(kind, text) {
  const id = ++toastId
  toasts.value = [...toasts.value.slice(-3), { id, kind, text }]
  setTimeout(() => dismissToast(id), kind === 'error' ? 8000 : 4000)
}

const confirmState = reactive({ open: false, title: '', body: '', confirmLabel: 'Confirm', resolve: null })
function askConfirm({ title, body, confirmLabel = 'Confirm' }) {
  return new Promise((resolve) => Object.assign(confirmState, { open: true, title, body, confirmLabel, resolve }))
}
function settleConfirm(value) {
  const resolve = confirmState.resolve
  confirmState.open = false
  confirmState.resolve = null
  if (resolve) resolve(value)
}

const lastUpdated = ref(null)
const stale = ref(false)
const refreshKey = ref(0)
const lastUpdatedLabel = computed(() => formatSastTime(lastUpdated.value))
let pollTimer = null
async function pollStatus() {
  if (document.visibilityState !== 'visible' || busy.value || loading.value || confirmState.open) return
  try {
    const { data } = await api.get('bootstrap')
    bootstrapData.value = data
    if (!['settings', 'trade'].includes(tab.value)) syncFromBootstrap()
  } catch {
    stale.value = true
  }
}
onMounted(() => {
  pollTimer = setInterval(pollStatus, 10000)
})
onBeforeUnmount(() => clearInterval(pollTimer))

const stats = computed(() => bootstrapData.value.stats || {})
const market = computed(() => bootstrapData.value.market || [])
const groupedMarket = computed(() => {
  const groups = new Map()
  for (const row of market.value) {
    const label = row.asset_class || 'Other markets'
    if (!groups.has(label)) groups.set(label, [])
    groups.get(label).push(row)
  }
  return Array.from(groups, ([label, rows]) => ({ label, rows }))
})
const signals = computed(() => bootstrapData.value.signals || [])
const portfolio = computed(() => bootstrapData.value.portfolio || [])
const tradeHistory = computed(() => bootstrapData.value.trade_history || [])
const autoTrader = computed(() => bootstrapData.value.auto_trader || {})
const openLiveTrades = computed(() => autoTrader.value.pending_live || [])
const liveStakedTotal = computed(() => openLiveTrades.value.reduce((sum, item) => sum + Number(item.stake || 0), 0))
const liveUnrealizedTotal = computed(() => {
  const vals = openLiveTrades.value.map((item) => item.unrealized_profit).filter((v) => v != null)
  return vals.length ? vals.reduce((sum, v) => sum + Number(v || 0), 0) : null
})
const openTradeCount = computed(() => portfolio.value.length + openLiveTrades.value.length)
const currentTrade = computed(() => {
  const live = openLiveTrades.value[0]
  if (live) {
    return {
      kind: 'Real-money trade',
      symbol: live.symbol || 'Market',
      direction: live.side || live.contract_type || 'Open',
      amount: live.stake ?? live.amount,
      started: live.opened_at || live.created_at || live.start_time,
    }
  }
  const practice = portfolio.value[0]
  if (practice) {
    return {
      kind: 'Practice trade',
      symbol: practice.symbol || 'Market',
      direction: practice.side || 'Open',
      amount: practice.exposure,
      started: practice.opened_at || practice.created_at || practice.entry_time,
    }
  }
  return null
})
const autoPerformance = computed(() => bootstrapData.value.auto_performance || {})

function sparkScale(points, height) {
  const low = Math.min(0, ...points)
  const span = Math.max(0, ...points) - low || 1
  return (value) => height - ((value - low) / span) * height
}

function sparkPath(points, width = 240, height = 48) {
  if (!points || points.length < 2) return ''
  const y = sparkScale(points, height)
  return points
    .map((value, index) => `${index ? 'L' : 'M'}${((index / (points.length - 1)) * width).toFixed(1)},${y(value).toFixed(1)}`)
    .join(' ')
}

function zeroLine(points, height = 48) {
  return points && points.length ? sparkScale(points, height)(0).toFixed(1) : height
}

async function startAuto(mode) {
  const payload = { mode }
  if (mode === 'live') {
    const ok = await askConfirm({
      title: binanceMainnet.value ? 'Start live trading on Binance MAINNET?' : `Start live auto-trading on ${networkLabel.value}?`,
      body: binanceMainnet.value
        ? 'This uses real funds. The bot places Spot buys and sells them after the hold time. Use an API key without withdrawal permission.'
        : 'The bot will place orders on your broker account without asking again. Stop is the kill switch.',
      confirmLabel: 'Start live',
    })
    if (!ok) return
    if (binanceMainnet.value) payload.confirm_mainnet = true
  }
  await runAction(() => api.post('auto/start', payload), `Auto-trader started (${mode}).`)
}

async function loadDemo() {
  await runAction(() => api.post('demo'), 'Demo data loaded: replay candles, paper trading only.')
  tab.value = 'dashboard'
}

async function forgetBinanceKeys() {
  await runAction(() => api.post('config', { clear_binance_keys: true }), 'Saved Binance keys removed.')
}

async function syncLivePositions() {
  await runAction(
    () => api.post('live-positions/sync'),
    'Synced open trades from Deriv.',
  )
}

const SETTINGS_BACKUP_KEY = 'fx-ace-settings-backup-v1'

function backupSettingsLocally() {
  try {
    const { deriv_token, binance_api_key, binance_api_secret, ...safeCopy } = settingsForm
    localStorage.setItem(SETTINGS_BACKUP_KEY, JSON.stringify({
      settings: safeCopy,
      symbolsJson: symbolsJson.value,
      allocationJson: allocationJson.value,
    }))
    addToast('Setup backed up in this browser (ready to restore after any deploy).', 'success')
  } catch {
    addToast('Could not save browser backup.', 'error')
  }
}

function restoreSettingsLocally() {
  try {
    const rawBackup = localStorage.getItem(SETTINGS_BACKUP_KEY)
    if (!rawBackup) {
      addToast('No saved browser backup found yet. Click Backup setup first.', 'info')
      return
    }
    const parsed = JSON.parse(rawBackup)
    if (parsed.settings) Object.assign(settingsForm, parsed.settings)
    if (parsed.symbolsJson) symbolsJson.value = parsed.symbolsJson
    if (parsed.allocationJson) allocationJson.value = parsed.allocationJson
    addToast('Setup restored from browser backup — click Save Settings to apply.', 'success')
  } catch {
    addToast('Could not read browser backup.', 'error')
  }
}

async function closeLivePosition(ref) {
  await runAction(() => api.post(`live-positions/${encodeURIComponent(ref)}/close`), 'Position sold.')
}

async function stopAuto() {
  await runAction(() => api.post('auto/stop'), 'Auto-trader stopped.')
}

async function exportJournal() {
  try {
    const { data } = await api.get('journal.csv', { responseType: 'blob' })
    const url = URL.createObjectURL(data)
    const link = document.createElement('a')
    link.href = url
    link.download = 'trade_log.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    setError(error)
  }
}
const rules = computed(() => bootstrapData.value.rules || [])
const rebalancePlan = computed(() => bootstrapData.value.rebalance_plan || { actions: [] })
const symbols = computed(() => bootstrapData.value.symbols || [])
const lastBacktest = computed(() => bootstrapData.value.last_backtest)
const candleBacktest = computed(() => lastBacktest.value?.candle_backtest || null)
const backtestOptions = ref({ horizon_bars: 5, candle_count: 1000, payout_pct: 0, fee_pct: null, use_guardian: true })

function formatRate(value) {
  return value === null || value === undefined ? '–' : `${(value * 100).toFixed(1)}%`
}
const lastBrokerStatus = computed(() => bootstrapData.value.last_broker_status || null)
const brokerSync = computed(() => bootstrapData.value.broker_sync || null)
const setupStatus = computed(() => bootstrapData.value.setup || {})
const lastScan = computed(() => bootstrapData.value.last_scan || null)
const scanFailed = computed(() => Boolean(lastScan.value?.errors?.length && !lastScan.value?.scanned))
const scanStatusMessage = computed(() => {
  if (!lastScan.value) return 'The bot has not checked the markets yet.'
  if (scanFailed.value) return 'The price provider is temporarily unavailable. No signals can be created until prices return.'
  return `Checked ${lastScan.value.scanned || 0} of ${lastScan.value.configured_symbols || 0} markets successfully.`
})
const ACTIVITY_LABELS = {
  AUTO_START: 'Automatic trading started',
  AUTO_STOP: 'Automatic trading stopped',
  AUTO_OPEN: 'Practice trade opened',
  AUTO_CLOSE: 'Practice trade closed',
  AUTO_ERROR: 'Live trade rejected',
  LIVE_ERROR: 'Live trade rejected',
  LIVE_OPEN: 'Real-money trade opened',
  LIVE_SETTLED: 'Real-money trade finished',
  PAPER_OPEN: 'Practice trade opened',
  PAPER_CLOSE: 'Practice trade closed',
}
function activityLabel(type) {
  return ACTIVITY_LABELS[type] || String(type || 'Activity').replaceAll('_', ' ').toLowerCase()
}
function activityMoneyKind(entry) {
  const type = String(entry?.type || '').toUpperCase()
  const note = String(entry?.note || '')
  if (type.startsWith('LIVE_') || type === 'AUTO_ERROR' || /\blive mode\b/i.test(note) || /^Live\b/i.test(note) || /\bcontract\s+\S+\s+settled\b/i.test(note)) {
    return 'live'
  }
  if (['AUTO_OPEN', 'AUTO_CLOSE', 'PAPER_OPEN', 'PAPER_CLOSE'].includes(type) || /\bpaper(?: mode)?\b/i.test(note)) {
    return 'practice'
  }
  return ''
}
function activityDisplayLabel(entry) {
  const kind = activityMoneyKind(entry)
  const note = String(entry?.note || '')
  if (entry.type === 'LIVE_OPEN' && /\b(?:DOT\w+|\(demo\)|demo\])/i.test(note)) {
    return 'Deriv demo trade opened'
  }
  if (entry.type === 'AUTO_START') return kind === 'live' ? 'Automatic live trading started' : 'Automatic practice started'
  if (entry.type === 'AUTO_STOP') return kind === 'live' ? 'Automatic live trading stopped' : kind === 'practice' ? 'Automatic practice stopped' : 'Automatic trading stopped'
  if (kind === 'live' && entry.type === 'AUTO_OPEN') return 'Live trade opened'
  return activityLabel(entry.type)
}
function activityAmountLabel(entry) {
  if (['AUTO_START', 'AUTO_STOP', 'AUTO_HALT', 'DAILY_SUMMARY'].includes(String(entry?.type || '').toUpperCase())) return ''
  const kind = activityMoneyKind(entry)
  if (kind === 'live') return 'Live stake'
  if (kind === 'practice') return 'Virtual exposure'
  return 'Recorded amount'
}
function activityPnl(entry) {
  const match = String(entry?.note || '').match(/P&L\s*([+-]?\d+(?:\.\d+)?)/i)
  return match ? Number(match[1]) : null
}
function activityOutcome(entry) {
  const pnl = activityPnl(entry)
  if (pnl === null) return ''
  if (pnl > 0) return 'profit'
  if (pnl < 0) return 'loss'
  return 'even'
}
function activityResult(entry) {
  const pnl = activityPnl(entry)
  if (pnl === null) return '—'
  return `${pnl > 0 ? '+' : pnl < 0 ? '−' : ''}${formatCurrency(Math.abs(pnl))}`
}
function sastDateKey(value) {
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Johannesburg', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(date)
}
const todayResults = computed(() => {
  const today = sastDateKey(new Date())
  const closedTypes = new Set(['AUTO_CLOSE', 'PAPER_CLOSE', 'REBALANCE_CLOSE', 'LIVE_SETTLED'])
  const results = tradeHistory.value
    .filter((entry) => closedTypes.has(entry.type) && sastDateKey(entry.timestamp) === today)
    .map(activityPnl)
    .filter((pnl) => pnl !== null)
  const gained = results.filter((pnl) => pnl > 0).reduce((sum, pnl) => sum + pnl, 0)
  const lost = Math.abs(results.filter((pnl) => pnl < 0).reduce((sum, pnl) => sum + pnl, 0))
  return {
    gained: Math.round(gained * 100) / 100,
    lost: Math.round(lost * 100) / 100,
    net: Math.round((gained - lost) * 100) / 100,
    wins: results.filter((pnl) => pnl > 0).length,
    losses: results.filter((pnl) => pnl < 0).length,
    closed: results.length,
  }
})
function friendlySystemError(message) {
  const text = String(message || '')
  if (/could not connect|handshake status|cloudflare|websocket/i.test(text)) {
    return 'Current prices are temporarily unavailable from the provider. The bot is waiting safely and will not open a trade without market data.'
  }
  return text || 'Something went wrong. Please try again.'
}
function friendlyDateTime(value) {
  return formatSastDateTime(value)
}
const liveTradingReady = computed(() => Boolean(setupStatus.value.live_trading_ready))
const activeBrokerAccountId = computed(
  () =>
    brokerSync.value?.account_id ||
    brokerSync.value?.loginid ||
    lastBrokerStatus.value?.account_id ||
    lastBrokerStatus.value?.loginid ||
    lastBrokerStatus.value?.options_account_id ||
    '',
)
const activeBrokerAccountType = computed(() => {
  const raw = String(
    brokerSync.value?.account_type || lastBrokerStatus.value?.account_type || '',
  )
    .trim()
    .toLowerCase()
  if (raw === 'real' || raw === 'demo') return raw
  const id = String(activeBrokerAccountId.value || '').toUpperCase()
  if (id.startsWith('DOT') || id.startsWith('VRTC')) return 'demo'
  if (id.startsWith('ROT') || id.startsWith('CR')) return 'real'
  return ''
})
const configuredDerivMode = computed(
  () => String(bootstrapData.value.settings?.deriv_options_account_mode || 'demo').trim().toLowerCase(),
)
const brokerModeMismatch = computed(() => Boolean(brokerSync.value?.account_mode_mismatch))
const brokerBalanceValue = computed(() => {
  if (brokerSync.value && brokerSync.value.balance !== null && brokerSync.value.balance !== undefined) {
    return Number(brokerSync.value.balance)
  }
  if (
    lastBrokerStatus.value &&
    lastBrokerStatus.value.balance !== null &&
    lastBrokerStatus.value.balance !== undefined &&
    !brokerModeMismatch.value
  ) {
    return Number(lastBrokerStatus.value.balance)
  }
  return null
})
const brokerBalanceCurrency = computed(
  () => brokerSync.value?.currency || lastBrokerStatus.value?.currency || 'USD',
)
const brokerBalanceLabel = computed(() => {
  if (brokerBalanceValue.value === null || Number.isNaN(brokerBalanceValue.value)) {
    return 'Unavailable'
  }
  return formatBrokerMoney(brokerBalanceValue.value, brokerBalanceCurrency.value)
})
const brokerApiStatusLabel = computed(() => {
  const status = brokerSync.value?.api_status
  if (status === 'connected') return 'Connected'
  if (status === 'connecting') return 'Connecting'
  if (status === 'reconnecting') return 'Reconnecting'
  if (status === 'error') return 'API Error'
  if (status === 'unconfigured') return 'Not Configured'
  if (lastBrokerStatus.value?.connected) return 'Connected'
  if (lastBrokerStatus.value?.checked_at) return 'Check Failed'
  return 'Not Tested'
})
const brokerSyncStatusLabel = computed(() => {
  if (brokerModeMismatch.value) return 'Account Mismatch'
  const status = brokerSync.value?.sync_status
  if (status === 'synced') return brokerSync.value?.subscription_active ? 'Synced (Live)' : 'Synced'
  if (status === 'syncing') return 'Syncing'
  if (status === 'stale') return 'Stale'
  if (status === 'error') return 'Sync Error'
  return 'Not Synced'
})
const brokerUpdateSourceLabel = computed(() => {
  const source = brokerSync.value?.last_update_source || lastBrokerStatus.value?.update_source || ''
  const labels = {
    subscription: 'Live WebSocket stream',
    purchase: 'Refreshed after purchase',
    settlement: 'Refreshed after settlement',
    refresh: 'Manual refresh',
    broker_test: 'Connection check',
    connect: 'Initial API sync',
  }
  return labels[source] || (source ? source : '—')
})
const brokerLastSyncedAt = computed(
  () =>
    brokerSync.value?.last_synced_at ||
    lastBrokerStatus.value?.balance_synced_at ||
    lastBrokerStatus.value?.checked_at ||
    null,
)
const brokerStatusLabel = computed(() => brokerApiStatusLabel.value)
const brokerTokenLabel = computed(() => {
  if (brokerSync.value?.token_mode === 'pat' || lastBrokerStatus.value?.token_mode === 'pat') {
    return 'PAT'
  }
  if (brokerSync.value?.token_mode === 'legacy' || lastBrokerStatus.value?.token_mode === 'legacy') {
    return 'Legacy'
  }
  if (settingsForm.deriv_token) {
    return settingsForm.deriv_token.startsWith('pat_') ? 'PAT' : 'Legacy'
  }
  return bootstrapData.value.settings?.deriv_token_mode === 'pat' ? 'PAT' : 'Legacy'
})

const derivTokenSaved = computed(() => Boolean(bootstrapData.value.settings?.deriv_token_set))
const binanceKeysSaved = computed(() => Boolean(bootstrapData.value.settings?.binance_key_set))
const brokerName = computed(() => bootstrapData.value.settings?.broker_provider || 'deriv')
const binanceMainnet = computed(
  () => brokerName.value === 'binance' && bootstrapData.value.settings?.binance_testnet === false,
)
const derivRealActive = computed(
  () => brokerName.value === 'deriv' && (activeBrokerAccountType.value === 'real' || configuredDerivMode.value === 'real'),
)
const networkLabel = computed(() => {
  if (brokerName.value === 'binance') return binanceMainnet.value ? 'Binance MAINNET' : 'Binance testnet'
  const acctType = (activeBrokerAccountType.value || configuredDerivMode.value || 'demo').toUpperCase()
  if (activeBrokerAccountId.value) {
    return `Deriv ${acctType} · ${activeBrokerAccountId.value}`
  }
  return `Deriv ${configuredDerivMode.value || 'demo'}`
})
watch(errorMessage, (value) => value && pushToast('error', value))
watch(successMessage, (value) => value && pushToast('success', value))
watch(bootstrapData, () => {
  lastUpdated.value = new Date()
  stale.value = false
})

const marketMap = computed(() => {
  return Object.fromEntries(market.value.map((row) => [row.symbol, row]))
})

const portfolioExposureLabel = computed(() => formatCurrency(stats.value.total_exposure || 0))
const paperEquityLabel = computed(() => formatCurrency(stats.value.paper_equity || 0))
const rebalanceDriftLabel = computed(() => formatPercent(rebalancePlan.value.drift_score || 0))
const passRateLabel = computed(() => {
  if (candleBacktest.value?.trades) {
    return `${formatRate(candleBacktest.value.win_rate)} of ${candleBacktest.value.trades}`
  }
  if (!lastBacktest.value?.scenario_total) {
    return 'Not run'
  }
  return `${lastBacktest.value.scenario_passes}/${lastBacktest.value.scenario_total}`
})

function clearMessages() {
  errorMessage.value = ''
  successMessage.value = ''
}

function setSuccess(message) {
  errorMessage.value = ''
  successMessage.value = message
}

function setError(error) {
  successMessage.value = ''
  errorMessage.value =
    error?.response?.data?.error ||
    error?.response?.data?.message ||
    error?.message ||
    'Unexpected error.'
}

function formatCurrency(value) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(Number(value || 0))
}

function formatBrokerMoney(value, currency = 'USD') {
  const num = Number(value)
  if (!Number.isFinite(num)) return 'Unavailable'
  const code = String(currency || 'USD').trim().toUpperCase()
  try {
    return `${new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: code,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(num)} ${code}`
  } catch {
    return `${num.toFixed(2)} ${code}`
  }
}

function onOptionsAccountModeChange() {
  const id = String(settingsForm.deriv_options_account_id || '').trim().toUpperCase()
  const mode = String(settingsForm.deriv_options_account_mode || 'demo').trim().toLowerCase()
  if (
    !id ||
    (mode === 'real' && (id.startsWith('DOT') || id.startsWith('VRTC'))) ||
    (mode === 'demo' && (id.startsWith('ROT') || id.startsWith('CR')))
  ) {
    settingsForm.deriv_options_account_id = ''
  }
}

function formatPercent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`
}

async function refreshBootstrapState() {
  const { data } = await api.get('bootstrap')
  bootstrapData.value = data
  syncFromBootstrap()
}

function syncFromBootstrap() {
  const settings = bootstrapData.value.settings || {}
  settingsForm.paper_trading_enabled = Boolean(settings.paper_trading_enabled)
  settingsForm.live_trading_enabled = Boolean(settings.live_trading_enabled)
  settingsForm.broker_provider = settings.broker_provider || 'deriv'
  settingsForm.deriv_app_id = settings.deriv_app_id || ''
  settingsForm.deriv_token = settings.deriv_token || ''
  settingsForm.market_source = settings.market_source || 'deriv'
  settingsForm.binance_data_url = settings.binance_data_url || 'https://data-api.binance.vision'
  settingsForm.binance_api_key = ''
  settingsForm.binance_api_secret = ''
  settingsForm.binance_testnet = settings.binance_testnet !== false
  settingsForm.deriv_api_url = settings.deriv_api_url || 'https://api.derivws.com'
  settingsForm.deriv_ws_url = settings.deriv_ws_url || 'wss://ws.derivws.com/websockets/v3'
  settingsForm.deriv_options_account_mode = settings.deriv_options_account_mode || 'demo'
  settingsForm.deriv_options_account_id = settings.deriv_options_account_id || ''
  settingsForm.risk_profile = settings.risk_profile || 'balanced'
  settingsForm.trade_direction = settings.trade_direction || 'both'
  settingsForm.daily_profit_target = Number(settings.daily_profit_target || 0)
  settingsForm.daily_loss_limit = Number(settings.daily_loss_limit || 0)
  settingsForm.auto_scan_interval_seconds = Number(settings.auto_scan_interval_seconds || 300)
  settingsForm.auto_hold_minutes = Number(settings.auto_hold_minutes || 25)
  settingsForm.auto_paper_exposure = Number(settings.auto_paper_exposure || 100)
  settingsForm.paper_starting_balance = Number(settings.paper_starting_balance || 10000)
  settingsForm.active_exit_enabled = Boolean(settings.active_exit_enabled)
  settingsForm.early_take_profit_pct = Number(settings.early_take_profit_pct ?? 50)
  settingsForm.early_stop_loss_pct = Number(settings.early_stop_loss_pct ?? 50)
  settingsForm.spike_grab_enabled = settings.spike_grab_enabled !== false
  settingsForm.spike_grab_min_profit_pct = Number(settings.spike_grab_min_profit_pct ?? 25)
  settingsForm.trailing_profit_enabled = settings.trailing_profit_enabled !== false
  settingsForm.trailing_arm_pct = Number(settings.trailing_arm_pct ?? 30)
  settingsForm.trailing_pullback_pct = Number(settings.trailing_pullback_pct ?? 12)
  settingsForm.reversal_exit_enabled = settings.reversal_exit_enabled !== false
  settingsForm.max_positions_per_symbol = Number(settings.max_positions_per_symbol || 1)
  settingsForm.scale_in_min_profit_pct = Number(settings.scale_in_min_profit_pct ?? 20)
  settingsForm.scale_in_cooldown_minutes = Number(settings.scale_in_cooldown_minutes ?? 5)
  settingsForm.guardian_check_interval_seconds = Number(settings.guardian_check_interval_seconds ?? 10)
  settingsForm.stop_and_reverse_enabled = Boolean(settings.stop_and_reverse_enabled)
  settingsForm.max_flips_per_day = Number(settings.max_flips_per_day ?? 3)
  settingsForm.dynamic_stake_enabled = Boolean(settings.dynamic_stake_enabled)
  settingsForm.dynamic_stake_pct = Number(settings.dynamic_stake_pct ?? 2)
  settingsForm.dynamic_stake_min = Number(settings.dynamic_stake_min ?? 0.5)
  settingsForm.dynamic_stake_max = Number(settings.dynamic_stake_max ?? 5)
  settingsForm.anchor_scalp_enabled = Boolean(settings.anchor_scalp_enabled)
  settingsForm.scalp_duration = Number(settings.scalp_duration ?? 30)
  settingsForm.scalp_duration_unit = settings.scalp_duration_unit || 'm'
  settingsForm.scalp_take_profit_pct = Number(settings.scalp_take_profit_pct ?? 30)
  settingsForm.mtf_confirm_enabled = Boolean(settings.mtf_confirm_enabled)
  settingsForm.mtf_granularity = Number(settings.mtf_granularity ?? 900)
  settingsForm.auto_clamp_duration_enabled = Boolean(settings.auto_clamp_duration_enabled)
  settingsForm.market_close_hour_utc = Number(settings.market_close_hour_utc ?? 21)
  settingsForm.deriv_contract_mode = settings.deriv_contract_mode || 'rise_fall'
  settingsForm.deriv_multiplier = Number(settings.deriv_multiplier ?? 100)
  settingsForm.max_trades_per_day = Number(settings.max_trades_per_day || 0)
  settingsForm.max_loss_streak = Number(settings.max_loss_streak || 0)
  settingsForm.loss_cooldown_minutes = Number(settings.loss_cooldown_minutes || 0)
  settingsForm.trading_hours_start = utcHourToSast(settings.trading_hours_start)
  settingsForm.trading_hours_end = utcHourToSast(settings.trading_hours_end)
  settingsForm.max_open_positions = Number(settings.max_open_positions || 6)
  settingsForm.live_trade_stake = Number(settings.live_trade_stake || 1)
  settingsForm.live_trade_currency = settings.live_trade_currency || 'USD'
  settingsForm.live_trade_duration = Number(settings.live_trade_duration || 5)
  settingsForm.live_trade_duration_unit = settings.live_trade_duration_unit || 't'
  settingsForm.rebalance_enabled = Boolean(settings.rebalance_enabled)
  settingsForm.rebalance_key_mode = settings.rebalance_key_mode || 'asset_class'
  settingsForm.rebalance_tolerance_pct = Number(settings.rebalance_tolerance || 0.08) * 100
  allocationJson.value = JSON.stringify(settings.target_allocations || {}, null, 2)
  symbolsJson.value = JSON.stringify(settings.symbols || [], null, 2)
  marketSnapshotsJson.value = JSON.stringify(settings.market_snapshots || [], null, 2)

  if (symbols.value.length) {
    if (!symbols.value.some((item) => item.symbol === tradeForm.symbol)) {
      tradeForm.symbol = symbols.value[0].symbol
    }
  } else {
    tradeForm.symbol = ''
  }
  if (!tradeForm.symbol && symbols.value.length) {
    tradeForm.symbol = symbols.value[0].symbol
  }
  if (symbols.value.length) {
    if (!symbols.value.some((item) => item.symbol === liveTradeForm.symbol)) {
      liveTradeForm.symbol = symbols.value[0].symbol
    }
  } else {
    liveTradeForm.symbol = ''
  }
  liveTradeForm.amount = Number(settings.live_trade_stake || 1)
  liveTradeForm.currency = settings.live_trade_currency || 'USD'
  liveTradeForm.duration = Number(settings.live_trade_duration || 5)
  liveTradeForm.duration_unit = settings.live_trade_duration_unit || 't'
  updateTradePrice()
}

function updateTradePrice() {
  const selected = marketMap.value[tradeForm.symbol]
  if (selected) {
    tradeForm.price = Number(selected.price)
    return
  }
  tradeForm.price = 0
}

async function fetchBootstrap() {
  try {
    await refreshBootstrapState()
  } catch (error) {
    setError(error)
  } finally {
    loading.value = false
  }
}

async function runAction(action, successText) {
  clearMessages()
  busy.value = true
  try {
    await action()
    await refreshBootstrapState()
    refreshKey.value += 1
    if (successText) {
      setSuccess(successText)
    }
  } catch (error) {
    setError(error)
  } finally {
    busy.value = false
  }
}

async function forgetDerivToken() {
  await runAction(() => api.post('config', { clear_deriv_token: true }), 'Saved Deriv token removed.')
}

function applyPreset(name) {
  if (name === 'safe') {
    Object.assign(settingsForm, {
      risk_profile: 'balanced',
      trade_direction: 'both',
      auto_scan_interval_seconds: 60,
      active_exit_enabled: true,
      early_take_profit_pct: 45,
      early_stop_loss_pct: 40,
      spike_grab_enabled: true,
      spike_grab_min_profit_pct: 22,
      trailing_profit_enabled: true,
      trailing_arm_pct: 28,
      trailing_pullback_pct: 10,
      reversal_exit_enabled: true,
      guardian_check_interval_seconds: 10,
      stop_and_reverse_enabled: false,
      max_positions_per_symbol: 1,
      anchor_scalp_enabled: false,
      mtf_confirm_enabled: true,
      mtf_granularity: 900,
      auto_clamp_duration_enabled: true,
      market_close_hour_utc: 21,
      live_trade_duration: 60,
      live_trade_duration_unit: 'm',
    })
    addToast('Safe Starter preset loaded — review and click Save Settings.', 'info')
  } else if (name === 'gold_max') {
    Object.assign(settingsForm, {
      risk_profile: 'balanced',
      trade_direction: 'both',
      auto_scan_interval_seconds: 60,
      active_exit_enabled: true,
      early_take_profit_pct: 55,
      early_stop_loss_pct: 45,
      spike_grab_enabled: true,
      spike_grab_min_profit_pct: 25,
      trailing_profit_enabled: true,
      trailing_arm_pct: 30,
      trailing_pullback_pct: 10,
      reversal_exit_enabled: true,
      guardian_check_interval_seconds: 10,
      stop_and_reverse_enabled: true,
      max_flips_per_day: 3,
      max_positions_per_symbol: 3,
      scale_in_min_profit_pct: 15,
      scale_in_cooldown_minutes: 5,
      anchor_scalp_enabled: true,
      scalp_duration: 30,
      scalp_duration_unit: 'm',
      scalp_take_profit_pct: 30,
      mtf_confirm_enabled: true,
      mtf_granularity: 900,
      auto_clamp_duration_enabled: true,
      market_close_hour_utc: 21,
      live_trade_duration: 3,
      live_trade_duration_unit: 'h',
      trading_hours_start: 9,
      trading_hours_end: 21,
      loss_cooldown_minutes: 5,
    })
    addToast('Profit Maximizer (Gold) preset loaded — review and click Save Settings.', 'info')
  } else if (name === 'scalper') {
    Object.assign(settingsForm, {
      risk_profile: 'aggressive',
      trade_direction: 'both',
      auto_scan_interval_seconds: 60,
      active_exit_enabled: true,
      early_take_profit_pct: 35,
      early_stop_loss_pct: 35,
      spike_grab_enabled: true,
      spike_grab_min_profit_pct: 20,
      trailing_profit_enabled: true,
      trailing_arm_pct: 25,
      trailing_pullback_pct: 8,
      reversal_exit_enabled: true,
      guardian_check_interval_seconds: 5,
      stop_and_reverse_enabled: true,
      max_flips_per_day: 4,
      max_positions_per_symbol: 2,
      scale_in_min_profit_pct: 15,
      scale_in_cooldown_minutes: 3,
      anchor_scalp_enabled: false,
      mtf_confirm_enabled: true,
      mtf_granularity: 900,
      auto_clamp_duration_enabled: true,
      market_close_hour_utc: 21,
      live_trade_duration: 30,
      live_trade_duration_unit: 'm',
    })
    addToast('Fast Scalper preset loaded — review and click Save Settings.', 'info')
  }
}

async function saveSettings() {
  let targetAllocations = {}
  let configuredSymbols = []
  let configuredMarketSnapshots = []
  try {
    targetAllocations = JSON.parse(allocationJson.value || '{}')
  } catch (error) {
    setError(new Error('Target allocations must be valid JSON.'))
    return
  }
  try {
    configuredSymbols = JSON.parse(symbolsJson.value || '[]')
    if (!Array.isArray(configuredSymbols)) {
      throw new Error('Configured symbols must be a JSON array.')
    }
  } catch (error) {
    setError(new Error('Configured symbols must be a valid JSON array.'))
    return
  }
  try {
    configuredMarketSnapshots = JSON.parse(marketSnapshotsJson.value || '[]')
    if (!Array.isArray(configuredMarketSnapshots)) {
      throw new Error('Market snapshots must be a JSON array.')
    }
  } catch (error) {
    setError(new Error('Market snapshots must be a valid JSON array.'))
    return
  }

  await runAction(
    () =>
      api.post('config', {
        paper_trading_enabled: settingsForm.paper_trading_enabled,
        live_trading_enabled: settingsForm.live_trading_enabled,
        broker_provider: settingsForm.broker_provider,
        deriv_app_id: settingsForm.deriv_app_id,
        deriv_token: settingsForm.deriv_token,
        market_source: settingsForm.market_source,
        binance_data_url: settingsForm.binance_data_url,
        binance_api_key: settingsForm.binance_api_key,
        binance_api_secret: settingsForm.binance_api_secret,
        binance_testnet: settingsForm.binance_testnet,
        deriv_api_url: settingsForm.deriv_api_url,
        deriv_ws_url: settingsForm.deriv_ws_url,
        deriv_options_account_mode: settingsForm.deriv_options_account_mode,
        deriv_options_account_id: settingsForm.deriv_options_account_id,
        risk_profile: settingsForm.risk_profile,
        trade_direction: settingsForm.trade_direction,
        daily_profit_target: Number(settingsForm.daily_profit_target),
        daily_loss_limit: Number(settingsForm.daily_loss_limit),
        auto_scan_interval_seconds: Number(settingsForm.auto_scan_interval_seconds),
        auto_hold_minutes: Number(settingsForm.auto_hold_minutes),
        auto_paper_exposure: Number(settingsForm.auto_paper_exposure),
        paper_starting_balance: Number(settingsForm.paper_starting_balance),
        active_exit_enabled: settingsForm.active_exit_enabled,
        early_take_profit_pct: Number(settingsForm.early_take_profit_pct),
        early_stop_loss_pct: Number(settingsForm.early_stop_loss_pct),
        spike_grab_enabled: settingsForm.spike_grab_enabled,
        spike_grab_min_profit_pct: Number(settingsForm.spike_grab_min_profit_pct),
        trailing_profit_enabled: settingsForm.trailing_profit_enabled,
        trailing_arm_pct: Number(settingsForm.trailing_arm_pct),
        trailing_pullback_pct: Number(settingsForm.trailing_pullback_pct),
        reversal_exit_enabled: settingsForm.reversal_exit_enabled,
        max_positions_per_symbol: Number(settingsForm.max_positions_per_symbol),
        scale_in_min_profit_pct: Number(settingsForm.scale_in_min_profit_pct),
        scale_in_cooldown_minutes: Number(settingsForm.scale_in_cooldown_minutes),
        guardian_check_interval_seconds: Number(settingsForm.guardian_check_interval_seconds),
        stop_and_reverse_enabled: settingsForm.stop_and_reverse_enabled,
        max_flips_per_day: Number(settingsForm.max_flips_per_day),
        dynamic_stake_enabled: settingsForm.dynamic_stake_enabled,
        dynamic_stake_pct: Number(settingsForm.dynamic_stake_pct),
        dynamic_stake_min: Number(settingsForm.dynamic_stake_min),
        dynamic_stake_max: Number(settingsForm.dynamic_stake_max),
        anchor_scalp_enabled: settingsForm.anchor_scalp_enabled,
        scalp_duration: Number(settingsForm.scalp_duration),
        scalp_duration_unit: settingsForm.scalp_duration_unit,
        scalp_take_profit_pct: Number(settingsForm.scalp_take_profit_pct),
        mtf_confirm_enabled: settingsForm.mtf_confirm_enabled,
        mtf_granularity: Number(settingsForm.mtf_granularity),
        auto_clamp_duration_enabled: settingsForm.auto_clamp_duration_enabled,
        market_close_hour_utc: Number(settingsForm.market_close_hour_utc),
        deriv_contract_mode: settingsForm.deriv_contract_mode,
        deriv_multiplier: Number(settingsForm.deriv_multiplier),
        max_trades_per_day: Number(settingsForm.max_trades_per_day),
        max_loss_streak: Number(settingsForm.max_loss_streak),
        loss_cooldown_minutes: Number(settingsForm.loss_cooldown_minutes),
        trading_hours_start: sastHourToUtc(settingsForm.trading_hours_start),
        trading_hours_end: sastHourToUtc(settingsForm.trading_hours_end),
        max_open_positions: settingsForm.max_open_positions,
        live_trade_stake: Number(settingsForm.live_trade_stake),
        live_trade_currency: settingsForm.live_trade_currency,
        live_trade_duration: Number(settingsForm.live_trade_duration),
        live_trade_duration_unit: settingsForm.live_trade_duration_unit,
        rebalance_enabled: settingsForm.rebalance_enabled,
        rebalance_key_mode: settingsForm.rebalance_key_mode,
        rebalance_tolerance: Number(settingsForm.rebalance_tolerance_pct || 0) / 100,
        target_allocations: targetAllocations,
        symbols: configuredSymbols,
        market_snapshots: configuredMarketSnapshots,
      }),
    'Settings saved.',
  )
}

async function scanMarket() {
  clearMessages()
  busy.value = true
  try {
    const { data } = await api.post('scan')
    await refreshBootstrapState()
    setSuccess(data?.message || 'Market scan complete.')
  } catch (error) {
    await refreshBootstrapState()
    setError(error)
  } finally {
    busy.value = false
  }
}

async function testBrokerConnection() {
  clearMessages()
  busy.value = true
  try {
    const { data } = await api.post('broker/test')
    await refreshBootstrapState()
    if (data?.ok) {
      setSuccess(data.message || 'Deriv connection verified.')
      return
    }
    setError(new Error(data?.message || 'Deriv connection failed.'))
  } catch (error) {
    await refreshBootstrapState()
    setError(error)
  } finally {
    busy.value = false
  }
}

async function refreshBrokerBalance() {
  clearMessages()
  busy.value = true
  try {
    const { data } = await api.post('broker/balance/refresh')
    await refreshBootstrapState()
    if (data?.ok) {
      setSuccess(data.message || 'Deriv account balance refreshed.')
      return
    }
    setError(new Error(data?.message || 'Could not refresh Deriv balance.'))
  } catch (error) {
    await refreshBootstrapState()
    setError(error)
  } finally {
    busy.value = false
  }
}

async function openPaperTrade() {
  await runAction(
    () =>
      api.post('paper-trades/open', {
        symbol: tradeForm.symbol,
        side: tradeForm.side,
        exposure: Number(tradeForm.exposure),
        price: Number(tradeForm.price),
      }),
    'Paper trade opened.',
  )
}

async function quickTrade(signal) {
  tradeForm.symbol = signal.symbol
  tradeForm.side = signal.decision
  tradeForm.exposure = 250
  tradeForm.price = Number(signal.price)
  await openPaperTrade()
}

async function submitLiveTrade() {
  clearMessages()
  busy.value = true
  liveOrderResult.value = null
  try {
    const { data } = await api.post('live-trades/open', {
      symbol: liveTradeForm.symbol,
      side: liveTradeForm.side,
      amount: Number(liveTradeForm.amount),
      currency: liveTradeForm.currency,
      duration: Number(liveTradeForm.duration),
      duration_unit: liveTradeForm.duration_unit,
    })
    liveOrderResult.value = data
    await refreshBootstrapState()
    setSuccess(data?.message || 'Live trade submitted.')
  } catch (error) {
    liveOrderResult.value = error?.response?.data || null
    await refreshBootstrapState()
    setError(error)
  } finally {
    busy.value = false
  }
}

async function quickLiveTrade(signal) {
  liveTradeForm.symbol = signal.symbol
  liveTradeForm.side = signal.decision
  await submitLiveTrade()
}

async function applyRebalance() {
  await runAction(() => api.post('paper-trades/rebalance'), 'Rebalance actions applied.')
}

async function runBacktests() {
  await runAction(() => api.post('backtests/run', backtestOptions.value), 'Backtests completed.')
}

async function resetDemo() {
  await runAction(() => api.post('reset'), 'Demo state reset.')
}

onMounted(fetchBootstrap)
</script>

<template>
  <main class="page-shell">
    <header class="topbar card">
      <div class="brand">
        <div class="eyebrow">Beginner-friendly practice</div>
        <h1>Market Guide</h1>
      </div>
      <div class="status-pills" aria-live="polite">
        <span class="pill" :class="{ 'pill-on': autoTrader.running && !scanFailed, 'pill-warn': autoTrader.running && scanFailed }">
          Automatic trading: {{ autoTrader.running ? (scanFailed ? 'waiting for prices' : 'on') : 'off' }}
        </span>
        <span class="pill">Market information: {{ bootstrapData.settings?.market_source || 'Deriv' }}</span>
        <span
          class="pill"
          :class="{
            'pill-danger': binanceMainnet || (derivRealActive && !brokerModeMismatch),
            'pill-warn': brokerModeMismatch,
          }"
        >
          {{ networkLabel }}
        </span>
        <span
          v-if="brokerName === 'deriv'"
          class="pill"
          :class="{
            'pill-on': brokerSync?.sync_status === 'synced' && !brokerModeMismatch,
            'pill-warn': brokerModeMismatch || brokerSync?.sync_status === 'stale' || brokerSync?.sync_status === 'error',
          }"
          :title="brokerLastSyncedAt ? `Last synced: ${friendlyDateTime(brokerLastSyncedAt)}` : 'Deriv balance synchronisation status'"
        >
          API: {{ brokerApiStatusLabel }} · Balance: {{ brokerSyncStatusLabel }}
        </span>
        <span class="pill" :class="{ 'pill-warn': stale }" :title="stale ? 'The latest update did not work' : 'Updates every 10 seconds'">
          {{ stale ? 'Information may be out of date' : 'Information is up to date' }} · {{ lastUpdatedLabel }}
        </span>
      </div>
      <div class="topbar-actions">
        <button v-if="autoTrader.running" class="button danger" :disabled="busy" @click="stopAuto">Stop automatic trading</button>
        <button class="button secondary" :disabled="busy" @click="loadDemo">Use sample data</button>
        <button
          class="button secondary icon-button"
          type="button"
          :aria-label="`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`"
          @click="theme = theme === 'dark' ? 'light' : 'dark'"
        >
          {{ theme === 'dark' ? '☀' : '☾' }}
        </button>
      </div>
    </header>
    <nav class="tabs" aria-label="Main navigation">
      <button
        v-for="item in TABS"
        :key="item.id"
        class="tab"
        :class="{ active: tab === item.id }"
        type="button"
        :aria-current="tab === item.id ? 'page' : undefined"
        @click="tab = item.id"
      >
        <span class="tab-icon" aria-hidden="true">{{ item.icon }}</span>
        <span>{{ item.label }}</span>
      </button>
    </nav>

    <aside class="page-helper" aria-live="polite">
      <div class="helper-heading">
        <div>
          <span class="helper-kicker">You are here</span>
          <h2>{{ currentTabHelp.title }}</h2>
        </div>
        <button class="helper-toggle" type="button" :aria-expanded="showPageHelp" @click="showPageHelp = !showPageHelp">
          {{ showPageHelp ? 'Hide help' : 'Show help' }}
        </button>
      </div>
      <template v-if="showPageHelp">
        <p>{{ currentTabHelp.text }}</p>
        <div class="helper-tip"><strong>Helpful to know:</strong> {{ currentTabHelp.tip }}</div>
      </template>
    </aside>

    <section
      v-if="tab === 'dashboard' || tab === 'trade'"
      class="trade-status"
      :class="{ 'trade-status-running': currentTrade }"
      aria-live="polite"
    >
      <div class="trade-status-icon" aria-hidden="true">
        <span v-if="currentTrade" class="status-pulse"></span>
        <span v-else>✓</span>
      </div>
      <div class="trade-status-copy">
        <span class="trade-status-label">Trade status</span>
        <template v-if="currentTrade">
          <h2>{{ currentTrade.kind }} is running</h2>
          <p>
            {{ currentTrade.symbol }} · {{ currentTrade.direction }}
            <template v-if="currentTrade.amount !== undefined"> · {{ formatCurrency(currentTrade.amount) }}</template>
            <template v-if="currentTrade.started"> · started {{ friendlyDateTime(currentTrade.started) }}</template>
          </p>
          <small v-if="openTradeCount > 1">You have {{ openTradeCount }} open trades in total.</small>
        </template>
        <template v-else>
          <h2>No trade is running</h2>
          <p>Your money is not currently in a trade. Start with a practice trade when you feel ready.</p>
        </template>
      </div>
      <button class="button secondary trade-status-action" type="button" @click="tab = currentTrade ? 'dashboard' : 'trade'">
        {{ currentTrade ? 'View open trade' : 'Start a practice trade' }}
      </button>
    </section>

    <section v-show="tab === 'dashboard'" class="hero-panel">
      <div class="stats-grid">
        <article class="card stat-card">
          <div class="stat-label">Possible trades found</div>
          <div class="stat-value">{{ stats.signal_count || 0 }}</div>
        </article>
        <article class="card stat-card">
          <div class="stat-label">Trades currently open</div>
          <div class="stat-value">{{ stats.open_positions || 0 }}</div>
        </article>
        <article class="card stat-card">
          <div class="stat-label">Money currently in trades</div>
          <div class="stat-value stat-value-sm">{{ portfolioExposureLabel }}</div>
        </article>
        <article v-if="brokerName === 'deriv'" class="card stat-card broker-balance-card">
          <div class="stat-label-row">
            <span class="stat-label">Deriv account balance</span>
            <span
              v-if="activeBrokerAccountType"
              class="account-type-badge"
              :class="activeBrokerAccountType === 'real' ? 'real' : 'demo'"
            >
              {{ activeBrokerAccountType.toUpperCase() }}
            </span>
          </div>
          <div class="stat-value stat-value-sm">{{ brokerBalanceLabel }}</div>
          <small class="broker-balance-meta">
            <span>Account: <strong>{{ activeBrokerAccountId || 'Not connected' }}</strong></span>
            <span>API: {{ brokerApiStatusLabel }} · {{ brokerSyncStatusLabel }}</span>
            <span v-if="brokerLastSyncedAt">Synced {{ friendlyDateTime(brokerLastSyncedAt) }} ({{ brokerUpdateSourceLabel }})</span>
          </small>
          <div v-if="brokerModeMismatch" class="status-banner error compact-banner">
            Configured mode is <strong>{{ configuredDerivMode }}</strong>, but last synced account is
            <strong>{{ activeBrokerAccountId }} ({{ activeBrokerAccountType }})</strong>. Refresh or test connection.
          </div>
          <div v-else-if="brokerSync?.last_error" class="status-banner error compact-banner">
            {{ brokerSync.last_error }}
          </div>
          <div class="stat-card-actions">
            <button
              class="button secondary compact-button"
              type="button"
              :disabled="busy || !derivTokenSaved"
              @click="refreshBrokerBalance"
            >
              Refresh balance
            </button>
          </div>
        </article>
        <article class="card stat-card">
          <div class="stat-label">Practice (paper) value</div>
          <div class="stat-value stat-value-sm">{{ paperEquityLabel }}</div>
          <small>
            Started {{ formatCurrency(stats.paper_starting_balance || 0) }} ·
            closed {{ formatCurrency(stats.paper_realized_pnl || 0) }} ·
            open {{ formatCurrency(stats.paper_unrealized_pnl || 0) }}
          </small>
        </article>
        <article class="card stat-card">
          <div class="stat-label">Portfolio change needed</div>
          <div class="stat-value">{{ rebalanceDriftLabel }}</div>
        </article>
        <article class="card stat-card">
          <div class="stat-label">Strategy test results</div>
          <div class="stat-value stat-value-sm">{{ passRateLabel }}</div>
        </article>
      </div>


      <div class="actions">
        <button class="button" :disabled="busy" @click="runBacktests">Test the strategy</button>
        <button class="button secondary" :disabled="busy" @click="applyRebalance">
          Apply suggested portfolio changes
        </button>
        <button class="button secondary" :disabled="busy" @click="scanMarket">Look for opportunities</button>
        <button class="button secondary" :disabled="busy" @click="resetDemo">Start practice again</button>
      </div>

      <div class="card section-card">
        <div class="section-head">
          <h2>Automatic trading</h2>
          <p>
            When switched on, the app looks for opportunities every {{ settingsForm.auto_scan_interval_seconds }} seconds
            and follows your safety limits. You can stop it at any time.
          </p>
        </div>
        <div class="section-body stack-list">
          <div class="item-row">
            <div class="item-title">
              {{ autoTrader.running ? (scanFailed ? 'Waiting for current prices' : `Running (${autoTrader.mode})`) : 'Stopped' }}
            </div>
            <div class="actions">
              <button class="button" :disabled="busy || autoTrader.running || scanFailed" @click="startAuto('paper')">Start practice automation</button>
              <button class="button secondary" :disabled="busy || autoTrader.running || scanFailed" @click="startAuto('live')">Start real-money automation</button>
              <button class="button secondary" :disabled="busy || !autoTrader.running" @click="stopAuto">Stop</button>
            <button v-if="brokerName === 'deriv'" class="button secondary" :disabled="busy" @click="syncLivePositions" title="Recover any open Deriv contracts after a deploy">Sync open trades</button>
            </div>
          </div>
          <div class="item-row">
            <span>Today’s result: {{ formatCurrency(autoTrader.realized_pnl || 0) }}</span>
            <span>{{ autoTrader.trades_today || 0 }} trades today · {{ (autoTrader.pending_live || []).length }} real-money trades waiting</span>
          </div>
          <div class="note">
            Practice size is a dollar notional, not currency units. A {{ formatCurrency(settingsForm.auto_paper_exposure) }}
            trade earns or loses about {{ formatCurrency(settingsForm.auto_paper_exposure * 0.001) }} for each 0.10% price move.
            Trades currently close after {{ settingsForm.auto_hold_minutes }} minutes, so small moves create small results.
          </div>
          <div v-for="item in autoTrader.pending_live || []" :key="`pl-${item.ref}`" class="item-row">
            <div>
              <strong>{{ item.symbol }} {{ item.side }}</strong>
              <span v-if="item.role" class="badge">{{ item.role }}</span>
              · {{ formatCurrency(item.stake) }} · ref {{ item.ref }}
              <div v-if="item.unrealized_profit != null || item.is_valid_to_sell != null" class="kv">
                <template v-if="item.unrealized_profit != null">
                  Live P&amp;L:
                  <span :class="Number(item.unrealized_profit) >= 0 ? 'positive' : 'negative'">
                    {{ formatCurrency(item.unrealized_profit) }}
                  </span>
                </template>
                <template v-if="item.peak_profit != null">
                  · Peak: {{ formatCurrency(item.peak_profit) }}
                </template>
                <template v-if="item.is_valid_to_sell != null">
                  · Early sell: <strong>{{ item.is_valid_to_sell ? 'Open' : 'Locked by Deriv' }}</strong>
                </template>
              </div>
            </div>
            <button class="button secondary" :disabled="busy" @click="closeLivePosition(item.ref)">
              {{ item.position ? 'Sell now' : 'Close early' }}
            </button>
          </div>
          <div v-if="autoTrader.halted_reason" class="status-banner error">{{ autoTrader.halted_reason }}</div>
          <div v-if="autoTrader.last_error" class="status-banner error">
            <strong>Prices unavailable</strong><br />{{ friendlySystemError(autoTrader.last_error) }}
          </div>
          <div v-if="autoTrader.paused_reason" class="note">Paused: {{ autoTrader.paused_reason }}</div>
          <div class="kv">Last checked: {{ friendlyDateTime(autoTrader.last_tick) }} · consecutive losing trades: {{ autoTrader.loss_streak || 0 }}</div>
          <div v-if="autoPerformance.trades" class="item-card">
            <div class="item-row">
              <div class="item-title">Performance (last {{ autoPerformance.trades }} closed trades)</div>
              <div class="kv">win {{ formatRate(autoPerformance.win_rate) }} · {{ formatCurrency(autoPerformance.pnl) }}</div>
            </div>
            <svg v-if="(autoPerformance.equity_curve || []).length > 1" viewBox="0 0 240 48" preserveAspectRatio="none" style="width: 100%; height: 48px" role="img" :aria-label="'Auto-trader equity curve'">
              <line x1="0" x2="240" :y1="zeroLine(autoPerformance.equity_curve)" :y2="zeroLine(autoPerformance.equity_curve)" stroke="currentColor" stroke-opacity="0.25" stroke-dasharray="4 4" />
              <path :d="sparkPath(autoPerformance.equity_curve)" fill="none" :stroke="(autoPerformance.equity_curve)[(autoPerformance.equity_curve).length - 1] >= 0 ? '#22c55e' : '#ef4444'" stroke-width="2" vector-effect="non-scaling-stroke" />
            </svg>
            <div v-for="row in autoPerformance.by_symbol" :key="`perf-${row.symbol}`" class="item-row">
              <span>{{ row.symbol }}</span>
              <span class="muted">{{ row.trades }} trades · win {{ formatRate(row.win_rate) }} · {{ formatCurrency(row.pnl) }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="card section-card">
        <div class="section-head">
          <h2>Is everything ready?</h2>
          <p>{{ scanFailed ? 'Your setup is saved, but live prices are temporarily unavailable.' : (setupStatus.recommended_next_step || 'Review the setup before trading.') }}</p>
        </div>
        <div class="section-body">
          <div class="stack-list">
            <div class="item-row">
              <span>Trading account connection</span>
              <span class="kv">{{ setupStatus.broker_configured ? 'Ready' : 'Needs setup' }}</span>
            </div>
            <div class="item-row">
              <span>Markets to watch</span>
              <span class="kv">{{ setupStatus.symbols_configured ? 'Ready' : 'Choose markets' }}</span>
            </div>
            <div class="item-row">
              <span>Current market prices</span>
              <span class="kv">{{ setupStatus.market_snapshots_configured ? 'Ready' : scanFailed ? 'Provider unavailable' : 'Waiting for data' }}</span>
            </div>
            <div class="item-row">
              <span>Practice trading</span>
              <span class="kv">{{ setupStatus.paper_trading_ready && !scanFailed ? 'Ready' : scanFailed ? 'Waiting for prices' : 'Needs setup' }}</span>
            </div>
            <div class="item-row">
              <span>Real-money trading</span>
              <span class="kv">{{ settingsForm.live_trading_enabled ? 'Enabled' : 'Safely off' }}</span>
            </div>
            <div class="item-row">
              <span>Real-money trading readiness</span>
              <span class="kv">{{ setupStatus.live_trading_ready && !scanFailed ? 'Ready' : 'Not ready' }}</span>
            </div>
            <div class="item-row">
              <span>Trading account status</span>
              <span class="kv">{{ brokerStatusLabel }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <div v-if="loading" class="card section-card">
      <div class="empty-state">Loading community edition bootstrap data...</div>
    </div>

    <template v-else>
      <div class="tab-grid">
      <CandleChart v-show="tab === 'dashboard'" :api="api" :symbols="symbols" :history="tradeHistory" :refresh-key="refreshKey" />
      <JournalTable v-show="tab === 'journal'" :api="api" :refresh-key="refreshKey" @export="exportJournal" />
      <section class="tab-section">
        <article v-show="tab === 'settings'" class="card section-card">
          <div class="section-head">
            <h2>Settings</h2>
            <p>Choose the main trading limits here. Technical connection options stay hidden unless you need them.</p>
          </div>
          <div class="section-body">
            <div class="settings-intro">
              <strong>Current setup: {{ settingsForm.risk_profile }} risk · {{ formatCurrency(settingsForm.auto_paper_exposure) }} per practice trade</strong>
              <span>{{ settingsForm.max_open_positions }} trades at once · held for {{ settingsForm.auto_hold_minutes }} minutes</span>
            </div>

            <details class="advanced-options settings-group">
              <summary>Connection and data source</summary>
              <p class="field-help">Open this only to change credentials, account, or price provider.</p>
            <div class="form-grid">
              <div class="field">
                <label>Broker Provider</label>
                <select v-model="settingsForm.broker_provider" class="select">
                  <option value="deriv">Deriv (rise/fall contracts)</option>
                  <option value="binance">Binance Spot (buy-only)</option>
                </select>
              </div>
              <div class="field">
                <label>Market Data Source</label>
                <select v-model="settingsForm.market_source" class="select">
                  <option value="deriv">Deriv candles</option>
                  <option value="binance">Binance public klines (no key)</option>
                  <option value="replay">Replay file (offline demo)</option>
                </select>
              </div>
              <div v-if="settingsForm.market_source === 'binance'" class="field">
                <label>Binance Data URL</label>
                <select v-model="settingsForm.binance_data_url" class="select">
                  <option value="https://data-api.binance.vision">data-api.binance.vision (global)</option>
                  <option value="https://api.binance.us">api.binance.us (US)</option>
                </select>
              </div>
              <template v-if="settingsForm.broker_provider === 'binance'">
                <div class="field">
                  <label>Binance Network</label>
                  <label class="check">
                    <input v-model="settingsForm.binance_testnet" type="checkbox" />
                    Testnet (recommended). Untick only for real funds.
                  </label>
                </div>
                <div class="field">
                  <label>Binance API Key</label>
                  <input
                    v-model="settingsForm.binance_api_key"
                    class="input"
                    type="password"
                    autocomplete="off"
                    :placeholder="binanceKeysSaved ? 'Saved — leave blank to keep it' : 'Spot trading only, no withdrawals'"
                  />
                </div>
                <div class="field">
                  <label>Binance API Secret</label>
                  <input
                    v-model="settingsForm.binance_api_secret"
                    class="input"
                    type="password"
                    autocomplete="off"
                    :placeholder="binanceKeysSaved ? 'Saved — leave blank to keep it' : 'Never shared back by the API'"
                  />
                  <button v-if="binanceKeysSaved" class="button secondary" type="button" :disabled="busy" @click="forgetBinanceKeys">
                    Forget saved keys
                  </button>
                </div>
              </template>
              <div class="field">
                <label>Deriv App ID</label>
                <input v-model="settingsForm.deriv_app_id" class="input" type="text" placeholder="Enter your Deriv app ID" />
              </div>
              <div class="field">
                <label>Deriv API Token</label>
                <input
                  v-model="settingsForm.deriv_token"
                  class="input"
                  type="password"
                  autocomplete="off"
                  :placeholder="derivTokenSaved ? 'Saved — leave blank to keep it' : 'Paste your Deriv token'"
                />
                <button
                  v-if="derivTokenSaved"
                  class="button secondary"
                  type="button"
                  :disabled="busy"
                  @click="forgetDerivToken"
                >
                  Forget saved token
                </button>
              </div>
              <div class="field">
                <label>Deriv REST URL</label>
                <input v-model="settingsForm.deriv_api_url" class="input" type="text" />
              </div>
              <div class="field">
                <label>Deriv WebSocket URL</label>
                <input v-model="settingsForm.deriv_ws_url" class="input" type="text" />
              </div>
              <div class="field">
                <label>Options Account Mode</label>
                <select
                  v-model="settingsForm.deriv_options_account_mode"
                  class="select"
                  @change="onOptionsAccountModeChange"
                >
                  <option value="demo">Demo</option>
                  <option value="real">Real</option>
                </select>
              </div>
              <div class="field">
                <label>Resolved Options Account ID</label>
                <input
                  v-model="settingsForm.deriv_options_account_id"
                  class="input"
                  type="text"
                  placeholder="Leave blank to auto-select by mode"
                />
                <small class="field-help">
                  Automatically cleared when switching between Demo and Real so a demo ID cannot override Real mode.
                </small>
              </div>
            </div>
            </details>

            <div class="preset-bar">
              <div class="preset-bar-head">
                <strong>Quick Setups</strong>
                <span>Pick a starting style in one click (keeps your Deriv login &amp; markets safe)</span>
              </div>
              <div class="preset-actions">
                <button class="preset-chip" type="button" @click="applyPreset('safe')">Safe Starter</button>
                <button class="preset-chip" type="button" @click="applyPreset('gold_max')">Profit Maximizer (Gold)</button>
                <button class="preset-chip" type="button" @click="applyPreset('scalper')">Fast Scalper</button>
                <button class="preset-chip" type="button" @click="backupSettingsLocally">Backup setup</button>
                <button class="preset-chip" type="button" @click="restoreSettingsLocally">Restore setup</button>
              </div>
            </div>

            <details class="ux-card" open>
              <summary>
                <div class="ux-card-title">
                  <strong><svg class="ux-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v10M9.5 9.5c0-1 1-1.5 2.5-1.5s2.5.6 2.5 1.7c0 2.3-5 1.8-5 4.3 0 1.1 1 1.8 2.5 1.8s2.5-.6 2.5-1.6"/></svg> 1. Trade Size &amp; Length</strong>
                  <span>How much to risk per trade and how long each trade runs</span>
                </div>
                <span class="ux-pill active">{{ settingsForm.live_trade_currency }} {{ settingsForm.live_trade_stake }} · {{ settingsForm.deriv_contract_mode === 'multiplier' ? (settingsForm.deriv_multiplier + 'x Multiplier') : (settingsForm.live_trade_duration + settingsForm.live_trade_duration_unit) }}</span>
              </summary>
              <div class="ux-card-body">
                <div class="form-grid">
                  <div class="field full-span">
                    <label class="checkbox-row">
                      <input v-model="settingsForm.live_trading_enabled" type="checkbox" />
                      <span>Real-money live trading enabled</span>
                    </label>
                    <small class="field-help">Must be checked and saved before starting real-money automation.</small>
                  </div>
                  <div class="field">
                    <label>How selective should entries be?</label>
                    <select v-model="settingsForm.risk_profile" class="select">
                      <option value="conservative">Very selective (Conservative)</option>
                      <option value="balanced">Balanced (Recommended)</option>
                      <option value="aggressive">More trades (Aggressive)</option>
                    </select>
                  </div>
                  <div class="field">
                    <label>Which way to trade</label>
                    <select v-model="settingsForm.trade_direction" class="select">
                      <option value="both">Buy and sell</option>
                      <option value="buy">Buy only</option>
                      <option value="sell">Sell only</option>
                    </select>
                  </div>
                  <div class="field">
                    <label>Amount per live trade</label>
                    <input v-model.number="settingsForm.live_trade_stake" class="input" type="number" min="0.35" step="0.01" />
                  </div>
                  <div class="field">
                    <label>Currency</label>
                    <input v-model="settingsForm.live_trade_currency" class="input" type="text" maxlength="10" />
                  </div>
                  <div class="field">
                    <label>Deriv contract type</label>
                    <select v-model="settingsForm.deriv_contract_mode" class="select">
                      <option value="rise_fall">Rise/Fall (fixed expiry)</option>
                      <option value="multiplier">Multipliers (close anytime, uncapped profit)</option>
                    </select>
                    <small class="field-help">Multipliers allow instant early close &amp; Guardian exits anytime.</small>
                  </div>
                  <div v-if="settingsForm.deriv_contract_mode === 'multiplier'" class="field">
                    <label>Multiplier strength</label>
                    <select v-model.number="settingsForm.deriv_multiplier" class="select">
                      <option :value="20">20x</option>
                      <option :value="40">40x</option>
                      <option :value="50">50x</option>
                      <option :value="100">100x (Standard)</option>
                      <option :value="200">200x</option>
                      <option :value="400">400x</option>
                      <option :value="500">500x</option>
                    </select>
                    <small class="field-help">Higher multiplier = faster profit &amp; faster stop-out.</small>
                  </div>
                  <template v-else>
                    <div class="field">
                      <label>How long each trade lasts</label>
                      <input v-model.number="settingsForm.live_trade_duration" class="input" type="number" min="1" max="365" />
                    </div>
                    <div class="field">
                      <label>Time unit</label>
                      <select v-model="settingsForm.live_trade_duration_unit" class="select">
                        <option value="t">Ticks</option>
                        <option value="s">Seconds</option>
                        <option value="m">Minutes</option>
                        <option value="h">Hours</option>
                        <option value="d">Days</option>
                      </select>
                    </div>
                  </template>
                  <div class="field full-span">
                    <label class="checkbox-row">
                      <input v-model="settingsForm.dynamic_stake_enabled" type="checkbox" />
                      <span>Grow trade size automatically with my balance</span>
                    </label>
                    <small class="field-help">Uses a % of your live Deriv balance so profits compound automatically.</small>
                  </div>
                  <div v-if="settingsForm.dynamic_stake_enabled" class="field full-span">
                    <label>Balance % / Minimum $ / Maximum $</label>
                    <div class="inline-actions">
                      <div class="mini-field"><span>Balance %</span><input v-model.number="settingsForm.dynamic_stake_pct" class="input" type="number" min="0.1" max="25" step="0.1" /></div>
                      <div class="mini-field"><span>Min ($)</span><input v-model.number="settingsForm.dynamic_stake_min" class="input" type="number" min="0.35" max="1000" step="0.05" /></div>
                      <div class="mini-field"><span>Max ($)</span><input v-model.number="settingsForm.dynamic_stake_max" class="input" type="number" min="0.35" max="10000" step="0.5" /></div>
                    </div>
                  </div>
                  <div class="field full-span">
                    <label class="checkbox-row">
                      <input v-model="settingsForm.auto_clamp_duration_enabled" type="checkbox" />
                      <span>Shorten long trades before daily market close</span>
                    </label>
                    <small class="field-help">Prevents evening rejections by trimming long trades (e.g. 5h) to fit before close.</small>
                  </div>
                  <div v-if="settingsForm.auto_clamp_duration_enabled" class="field">
                    <label>Market close hour (UTC)</label>
                    <input v-model.number="settingsForm.market_close_hour_utc" class="input" type="number" min="0" max="23" />
                    <small class="field-help">21 UTC = 23:00 SAST (Deriv Gold daily close).</small>
                  </div>
                </div>
              </div>
            </details>

            <details class="ux-card" open>
              <summary>
                <div class="ux-card-title">
                  <strong><svg class="ux-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l7 4v5c0 5-3.5 8-7 9-3.5-1-7-4-7-9V7l7-4z"/></svg> 2. Profit Lock &amp; Protection</strong>
                  <span>Bank big candle spikes before retests, trail winners, or cut bad trades early</span>
                </div>
                <span class="ux-pill" :class="{ active: settingsForm.active_exit_enabled }">
                  {{ settingsForm.active_exit_enabled ? ('ON · +' + settingsForm.early_take_profit_pct + '% / -' + settingsForm.early_stop_loss_pct + '%') : 'OFF' }}
                </span>
              </summary>
              <div class="ux-card-body">
                <div class="form-grid">
                  <div class="field full-span">
                    <label class="checkbox-row">
                      <input v-model="settingsForm.active_exit_enabled" type="checkbox" />
                      <span>Watch open trades &amp; exit early (like a human eye)</span>
                    </label>
                  </div>
                  <template v-if="settingsForm.active_exit_enabled">
                    <div class="field">
                      <label>Take profit early at (%)</label>
                      <input v-model.number="settingsForm.early_take_profit_pct" class="input" type="number" min="0" max="500" step="1" />
                      <small class="field-help">Banks profit early and frees the slot. 0 = off.</small>
                    </div>
                    <div class="field">
                      <label>Cut loss early at (%)</label>
                      <input v-model.number="settingsForm.early_stop_loss_pct" class="input" type="number" min="0" max="100" step="1" />
                      <small class="field-help">Saves part of your stake instead of losing 100%. 0 = off.</small>
                    </div>
                    <div class="field">
                      <label>Check live profit every (seconds)</label>
                      <input v-model.number="settingsForm.guardian_check_interval_seconds" class="input" type="number" min="5" max="60" step="1" />
                      <small class="field-help">10s catches fast spikes and pullbacks in real time.</small>
                    </div>
                    <div class="field">
                      <label>Min profit to grab a spike (%)</label>
                      <input v-model.number="settingsForm.spike_grab_min_profit_pct" class="input" type="number" min="1" max="500" step="1" :disabled="!settingsForm.spike_grab_enabled" />
                      <small class="field-help">Locks profit on big stretched candles before they retest.</small>
                    </div>
                    <div class="field full-span">
                      <label class="checkbox-row">
                        <input v-model="settingsForm.spike_grab_enabled" type="checkbox" />
                        <span>Grab profit on long stretched candles before pullback</span>
                      </label>
                    </div>
                    <div class="field full-span">
                      <label class="checkbox-row">
                        <input v-model="settingsForm.trailing_profit_enabled" type="checkbox" />
                        <span>Let big winners run, then close on pullback</span>
                      </label>
                    </div>
                    <div v-if="settingsForm.trailing_profit_enabled" class="field full-span">
                      <label>Start trailing at (+%) / Close if profit drops by (%)</label>
                      <div class="inline-actions">
                        <div class="mini-field"><span>Start trailing at (+%)</span><input v-model.number="settingsForm.trailing_arm_pct" class="input" type="number" min="1" max="500" step="1" /></div>
                        <div class="mini-field"><span>Close on drop (%)</span><input v-model.number="settingsForm.trailing_pullback_pct" class="input" type="number" min="1" max="200" step="1" /></div>
                      </div>
                      <small class="field-help">Example: 30 / 10 starts tracking at +30% and closes if it drops 10% from the peak.</small>
                    </div>
                    <div class="field full-span">
                      <label class="checkbox-row">
                        <input v-model="settingsForm.reversal_exit_enabled" type="checkbox" />
                        <span>Close early if the chart reverses direction</span>
                      </label>
                    </div>
                    <template v-if="settingsForm.reversal_exit_enabled">
                      <div class="field full-span">
                        <label class="checkbox-row">
                          <input v-model="settingsForm.stop_and_reverse_enabled" type="checkbox" />
                          <span>Flip straight into the new direction (Stop-and-Reverse)</span>
                        </label>
                      </div>
                      <div v-if="settingsForm.stop_and_reverse_enabled" class="field">
                        <label>Max direction flips per day</label>
                        <input v-model.number="settingsForm.max_flips_per_day" class="input" type="number" min="0" max="50" step="1" />
                        <small class="field-help">Stops choppy markets from flipping back and forth. 0 = unlimited.</small>
                      </div>
                    </template>
                  </template>
                </div>
              </div>
            </details>

            <details class="ux-card" open>
              <summary>
                <div class="ux-card-title">
                  <strong><svg class="ux-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 17l6-6 4 4 8-8"/><path d="M17 7h4v4"/></svg> 3. Extra Trades &amp; Big-Picture Check</strong>
                  <span>Stack winning trades, run quick scalps inside long trades, and confirm the bigger trend</span>
                </div>
                <span class="ux-pill" :class="{ active: settingsForm.max_positions_per_symbol > 1 || settingsForm.mtf_confirm_enabled }">
                  {{ settingsForm.max_positions_per_symbol }}x{{ settingsForm.anchor_scalp_enabled ? ' · Long+Quick' : '' }}{{ settingsForm.mtf_confirm_enabled ? ' · Trend check' : '' }}
                </span>
              </summary>
              <div class="ux-card-body">
                <div class="form-grid">
                  <div class="field">
                    <label>Max open trades (all markets)</label>
                    <input v-model.number="settingsForm.max_open_positions" class="input" type="number" min="1" max="20" />
                  </div>
                  <div class="field">
                    <label>Max trades at once on one market</label>
                    <input v-model.number="settingsForm.max_positions_per_symbol" class="input" type="number" min="1" max="10" />
                    <small class="field-help">1 = single trade. 2–3 lets the bot add to winning trends.</small>
                  </div>
                  <template v-if="settingsForm.max_positions_per_symbol > 1">
                    <div class="field full-span">
                      <label>Add next trade after (+%) profit / wait (minutes)</label>
                      <div class="inline-actions">
                        <div class="mini-field"><span>Min profit (+%)</span><input v-model.number="settingsForm.scale_in_min_profit_pct" class="input" type="number" min="0" max="500" step="1" /></div>
                        <div class="mini-field"><span>Wait between trades (min)</span><input v-model.number="settingsForm.scale_in_cooldown_minutes" class="input" type="number" min="0" max="1440" step="1" /></div>
                      </div>
                    </div>
                    <div class="field full-span">
                      <label class="checkbox-row">
                        <input v-model="settingsForm.anchor_scalp_enabled" type="checkbox" />
                        <span>Long trend trade + quick profit trades in between</span>
                      </label>
                      <small class="field-help">Trade #1 rides your long length (e.g. 3–5h); trades #2+ open shorter scalps and bank quick profits.</small>
                    </div>
                    <div v-if="settingsForm.anchor_scalp_enabled" class="field full-span">
                      <label>Quick trade length &amp; take-profit (%)</label>
                      <div class="inline-actions">
                        <div class="mini-field"><span>Length</span><input v-model.number="settingsForm.scalp_duration" class="input" type="number" min="1" max="1440" step="1" /></div>
                        <div class="mini-field"><span>Unit</span><select v-model="settingsForm.scalp_duration_unit" class="select"><option value="m">Minutes</option><option value="h">Hours</option><option value="s">Seconds</option><option value="t">Ticks</option></select></div>
                        <div class="mini-field"><span>Quick TP (%)</span><input v-model.number="settingsForm.scalp_take_profit_pct" class="input" type="number" min="0" max="500" step="1" /></div>
                      </div>
                    </div>
                  </template>
                  <div class="field full-span">
                    <label class="checkbox-row">
                      <input v-model="settingsForm.mtf_confirm_enabled" type="checkbox" />
                      <span>Check bigger chart trend before entering</span>
                    </label>
                    <small class="field-help">Avoids buying into a bigger downtrend (or selling into a bigger uptrend).</small>
                  </div>
                  <div v-if="settingsForm.mtf_confirm_enabled" class="field">
                    <label>Bigger chart timeframe</label>
                    <select v-model.number="settingsForm.mtf_granularity" class="select">
                      <option :value="900">15 minutes</option>
                      <option :value="1800">30 minutes</option>
                      <option :value="3600">1 hour</option>
                      <option :value="14400">4 hours</option>
                    </select>
                  </div>
                </div>
              </div>
            </details>

            <details class="ux-card">
              <summary>
                <div class="ux-card-title">
                  <strong><svg class="ux-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg> 4. Trading Hours &amp; Daily Limits</strong>
                  <span>When the bot is allowed to trade, rest timers, and daily profit/loss stops</span>
                </div>
                <span class="ux-pill active">
                  <svg class="ux-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg> {{ settingsForm.trading_hours_start === settingsForm.trading_hours_end ? 'All day' : (String(settingsForm.trading_hours_start).padStart(2, '0') + ':00–' + String(settingsForm.trading_hours_end).padStart(2, '0') + ':00 SAST') }}
                </span>
              </summary>
              <div class="ux-card-body">
                <div class="form-grid">
                  <div class="field">
                    <label>Start trading at (SAST hour)</label>
                    <input v-model.number="settingsForm.trading_hours_start" class="input" type="number" min="0" max="23" />
                    <small class="field-help">9 = 09:00 SAST (London open).</small>
                  </div>
                  <div class="field">
                    <label>Stop trading at (SAST hour)</label>
                    <input v-model.number="settingsForm.trading_hours_end" class="input" type="number" min="0" max="23" />
                    <small class="field-help">21 = 21:00 SAST. Same hour = all day.</small>
                  </div>
                  <div class="field">
                    <label>Scan charts every (seconds)</label>
                    <input v-model.number="settingsForm.auto_scan_interval_seconds" class="input" type="number" min="30" max="3600" />
                  </div>
                  <div class="field">
                    <label>Rest after a loss (minutes)</label>
                    <input v-model.number="settingsForm.loss_cooldown_minutes" class="input" type="number" min="0" max="1440" />
                  </div>
                  <div class="field">
                    <label>Daily profit goal ($)</label>
                    <input v-model.number="settingsForm.daily_profit_target" class="input" type="number" min="0" step="0.01" />
                    <small class="field-help">Stops trading once reached. 0 = no limit.</small>
                  </div>
                  <div class="field">
                    <label>Max daily loss ($)</label>
                    <input v-model.number="settingsForm.daily_loss_limit" class="input" type="number" min="0" step="0.01" />
                    <small class="field-help">Protects your account on bad days. 0 = no limit.</small>
                  </div>
                  <div class="field">
                    <label>Max trades per day</label>
                    <input v-model.number="settingsForm.max_trades_per_day" class="input" type="number" min="0" max="1000" />
                    <small class="field-help">0 = unlimited.</small>
                  </div>
                  <div class="field">
                    <label>Stop after losses in a row</label>
                    <input v-model.number="settingsForm.max_loss_streak" class="input" type="number" min="0" max="100" />
                    <small class="field-help">0 = keep trading.</small>
                  </div>
                </div>
              </div>
            </details>

            <details class="ux-card">
              <summary>
                <div class="ux-card-title">
                  <strong><svg class="ux-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/></svg> 5. Live / Practice Mode &amp; Demo</strong>
                  <span>Switch real money or practice mode on/off, and demo balance settings</span>
                </div>
                <span class="ux-pill" :class="{ active: settingsForm.live_trading_enabled }">
                  {{ settingsForm.live_trading_enabled ? 'Live ON' : 'Practice only' }}
                </span>
              </summary>
              <div class="ux-card-body">
                <div class="toggle-row">
                  <label class="check-line">
                    <input v-model="settingsForm.paper_trading_enabled" type="checkbox" />
                    <span>Practice mode enabled</span>
                  </label>
                  <label class="check-line">
                    <input v-model="settingsForm.live_trading_enabled" type="checkbox" />
                    <span>Real-money live trading enabled</span>
                  </label>
                  <label class="check-line">
                    <input v-model="settingsForm.rebalance_enabled" type="checkbox" />
                    <span>Auto portfolio rebalance</span>
                  </label>
                </div>
                <div class="form-grid">
                  <div class="field">
                    <label>Close practice trades after (min)</label>
                    <input v-model.number="settingsForm.auto_hold_minutes" class="input" type="number" min="1" max="1440" />
                  </div>
                  <div class="field">
                    <label>Amount per practice trade ($)</label>
                    <input v-model.number="settingsForm.auto_paper_exposure" class="input" type="number" min="1" />
                  </div>
                  <div class="field">
                    <label>Demo starting balance ($)</label>
                    <input v-model.number="settingsForm.paper_starting_balance" class="input" type="number" min="1" step="100" />
                  </div>
                  <div class="field">
                    <label>Rebalance tolerance (%)</label>
                    <input v-model.number="settingsForm.rebalance_tolerance_pct" class="input" type="number" min="0" max="50" step="0.5" />
                  </div>
                  <div class="field">
                    <label>Allocation grouping</label>
                    <select v-model="settingsForm.rebalance_key_mode" class="select">
                      <option value="asset_class">Asset Class</option>
                      <option value="symbol">Symbol</option>
                    </select>
                  </div>
                </div>
              </div>
            </details>

            <h3 class="settings-subtitle">Markets to watch</h3>
            <SymbolPicker v-model="symbolsJson" :source="settingsForm.market_source" />

            <details class="advanced-options settings-group">
              <summary>Advanced portfolio configuration</summary>
              <div class="field">
                <label>Target Allocations JSON</label>
                <textarea v-model="allocationJson" class="textarea code-block" spellcheck="false" placeholder='{"FOREX": 0.4, "SYNTHETICS": 0.25}' />
              </div>
              <div class="field">
                <label>Configured Symbols JSON</label>
                <textarea v-model="symbolsJson" class="textarea code-block" spellcheck="false" placeholder='[{"symbol":"frxEURUSD","label":"EUR/USD","asset_class":"FOREX"}]' />
              </div>
            </details>

            <div class="market-data-status" :class="{ 'market-data-ready': market.length, 'market-data-error': !market.length && lastScan?.errors?.length }">
              <div>
                <strong>{{ market.length ? 'Market information is ready' : lastScan?.errors?.length ? 'Market information could not be loaded' : 'Waiting for market information' }}</strong>
                <p v-if="market.length">The bot has current information for {{ market.length }} market{{ market.length === 1 ? '' : 's' }} and can look for opportunities.</p>
                <p v-else-if="lastScan?.errors?.length">The provider did not return prices. Your settings are saved and safe. Try “Look for opportunities” again in a moment.</p>
                <p v-else>Add at least one market, save your setup, then ask the bot to look for opportunities.</p>
              </div>
            </div>

            <details class="advanced-options">
              <summary>Advanced: generated market data</summary>
              <div class="field">
                <label>Market data used by the strategy</label>
                <textarea
                  v-model="marketSnapshotsJson"
                  class="textarea code-block"
                  spellcheck="false"
                  placeholder="Generated automatically after a successful market scan"
                />
                <small class="field-help">You normally do not need to edit this. An empty [] means the provider has not returned prices yet.</small>
              </div>
            </details>

            <div class="actions">
              <button class="button" :disabled="busy" @click="saveSettings">Save Settings</button>
              <button
                class="button secondary"
                :disabled="busy || !symbols.length"
                @click="scanMarket"
              >
                Look for opportunities now
              </button>
            </div>
          </div>
        </article>

        <article v-show="tab === 'settings'" class="card section-card">
          <div class="section-head">
            <h2>Broker Connection</h2>
            <p>Verify your saved Deriv settings before attempting a live order.</p>
          </div>
          <div class="section-body">
            <div class="item-card">
              <div class="item-row">
                <div class="item-title">API Connection</div>
                <span
                  class="signal-pill"
                  :class="
                    brokerSync?.connected || lastBrokerStatus?.connected
                      ? 'buy'
                      : brokerSync?.api_status === 'error' || lastBrokerStatus?.checked_at
                        ? 'sell'
                        : 'hold'
                  "
                >
                  {{ brokerApiStatusLabel }}
                </span>
              </div>
              <div class="item-row">
                <span class="muted">Balance synchronisation</span>
                <span class="kv">{{ brokerSyncStatusLabel }}</span>
              </div>
              <div class="item-row">
                <span class="muted">Real-time balance stream</span>
                <span class="kv">{{ brokerSync?.subscription_active ? 'Subscribed (active)' : 'Not subscribed' }}</span>
              </div>
              <div class="item-row">
                <span class="muted">Active account ID</span>
                <span class="kv">{{ activeBrokerAccountId || 'Unknown' }}</span>
              </div>
              <div class="item-row">
                <span class="muted">Account type</span>
                <span class="kv">
                  <span
                    v-if="activeBrokerAccountType"
                    class="account-type-badge"
                    :class="activeBrokerAccountType === 'real' ? 'real' : 'demo'"
                  >
                    {{ activeBrokerAccountType.toUpperCase() }}
                  </span>
                  <template v-else>Unknown</template>
                  (configured: {{ configuredDerivMode }})
                </span>
              </div>
              <div class="item-row">
                <span class="muted">Deriv API balance</span>
                <span class="kv">{{ brokerBalanceLabel }}</span>
              </div>
              <div class="item-row">
                <span class="muted">Last synced</span>
                <span class="kv">{{ friendlyDateTime(brokerLastSyncedAt) }}</span>
              </div>
              <div class="item-row">
                <span class="muted">Last update source</span>
                <span class="kv">{{ brokerUpdateSourceLabel }}</span>
              </div>
              <div class="item-row">
                <span class="muted">Token mode</span>
                <span class="kv">{{ brokerTokenLabel }}</span>
              </div>
              <div v-if="brokerModeMismatch" class="status-banner error compact-banner">
                Account mode mismatch: configured for <strong>{{ configuredDerivMode }}</strong>,
                but connected account is <strong>{{ activeBrokerAccountId }} ({{ activeBrokerAccountType }})</strong>.
              </div>
              <div v-if="brokerSync?.last_error" class="status-banner error compact-banner">
                {{ brokerSync.last_error }}
              </div>
              <p class="note">
                {{ lastBrokerStatus?.message || 'Deriv balance is retrieved directly from the Deriv API and never estimated from journal P&L.' }}
              </p>
            </div>
            <div class="actions">
              <button class="button" :disabled="busy" @click="testBrokerConnection">Test Deriv Connection</button>
              <button class="button secondary" :disabled="busy || !derivTokenSaved" @click="refreshBrokerBalance">
                Refresh Balance
              </button>
              <button class="button secondary" :disabled="busy" @click="fetchBootstrap">Refresh Dashboard</button>
            </div>
            <div class="empty-state">
              PAT tokens use the configured Deriv options-account flow. Legacy tokens use WebSocket authorize.
            </div>
          </div>
        </article>
      </section>

      <section class="tab-section">
        <article v-show="tab === 'trade'" class="card section-card">
          <div class="section-head">
            <h2>Paper Trade Entry</h2>
            <p>Open paper positions against the symbols and prices you configured.</p>
          </div>
          <div class="section-body">
            <div class="form-grid">
              <div class="field">
                <label>Symbol</label>
                <select v-model="tradeForm.symbol" class="select" @change="updateTradePrice">
                  <option v-for="item in symbols" :key="item.symbol" :value="item.symbol">
                    {{ item.label }} ({{ item.asset_class }})
                  </option>
                </select>
              </div>
              <div class="field">
                <label>Side</label>
                <select v-model="tradeForm.side" class="select">
                  <option value="BUY">BUY</option>
                  <option value="SELL">SELL</option>
                </select>
              </div>
              <div class="field">
                <label>Exposure</label>
                <input v-model.number="tradeForm.exposure" class="input" type="number" min="50" step="50" />
              </div>
              <div class="field">
                <label>Entry Price</label>
                <input v-model.number="tradeForm.price" class="input" type="number" min="0.0001" step="0.0001" />
              </div>
            </div>
            <div class="actions">
              <button class="button" :disabled="busy || !tradeForm.symbol" @click="openPaperTrade">Open Paper Trade</button>
            </div>
            <div v-if="!symbols.length" class="empty-state">
              Add at least one symbol in Settings before opening a paper trade.
            </div>
          </div>
        </article>

        <article v-show="tab === 'trade'" class="card section-card">
          <div class="section-head">
            <h2>Live Trade Entry</h2>
            <p v-if="brokerName === 'binance'">
              Market BUY on {{ networkLabel }} sized by the stake in quote currency (e.g. USDT). Spot is buy-only; open
              positions are sold by the auto-trader after the hold time or with "Sell now" on the dashboard.
            </p>
            <p v-else>Send an optional Deriv order using the saved broker settings and live defaults.</p>
          </div>
          <div class="section-body">
            <div class="form-grid">
              <div class="field">
                <label>Symbol</label>
                <select v-model="liveTradeForm.symbol" class="select">
                  <option v-for="item in symbols" :key="`live-${item.symbol}`" :value="item.symbol">
                    {{ item.label }} ({{ item.asset_class }})
                  </option>
                </select>
              </div>
              <div class="field">
                <label>Direction</label>
                <select v-model="liveTradeForm.side" class="select">
                  <option value="BUY">BUY / CALL</option>
                  <option value="SELL">SELL / PUT</option>
                </select>
              </div>
              <div class="field">
                <label>Stake</label>
                <input v-model.number="liveTradeForm.amount" class="input" type="number" min="0.35" step="0.01" />
              </div>
              <div class="field">
                <label>Currency</label>
                <input v-model="liveTradeForm.currency" class="input" type="text" maxlength="10" />
              </div>
              <div class="field">
                <label>Duration</label>
                <input v-model.number="liveTradeForm.duration" class="input" type="number" min="1" max="365" />
              </div>
              <div class="field">
                <label>Unit</label>
                <select v-model="liveTradeForm.duration_unit" class="select">
                  <option value="t">Ticks</option>
                  <option value="s">Seconds</option>
                  <option value="m">Minutes</option>
                  <option value="h">Hours</option>
                  <option value="d">Days</option>
                </select>
              </div>
            </div>
            <div class="actions">
              <button class="button" :disabled="busy || !liveTradeForm.symbol || !liveTradingReady" @click="submitLiveTrade">
                Submit Live Trade
              </button>
            </div>
            <div class="empty-state">
              Live trading remains opt-in. Save settings, enable live trading, and test the broker connection first.
            </div>
            <div v-if="liveOrderResult" class="item-card">
              <div class="item-row">
                <div class="item-title">Last Live Response</div>
                <span class="kv">{{ liveOrderResult.message || liveOrderResult.error || 'Response captured' }}</span>
              </div>
              <div class="item-row">
                <span class="muted">Proposal</span>
                <span class="kv">
                  {{ liveOrderResult.proposal?.id || liveOrderResult.request?.symbol || 'Unavailable' }}
                </span>
              </div>
              <div class="item-row">
                <span class="muted">Contract</span>
                <span class="kv">
                  {{ liveOrderResult.buy?.contract_id || liveOrderResult.buy?.transaction_id || 'Pending' }}
                </span>
              </div>
            </div>
          </div>
        </article>
      </section>

      <section class="tab-section">
        <article v-show="tab === 'signals'" class="card section-card">
          <div class="section-head">
            <h2>Trade suggestions</h2>
            <p>The bot checks current prices and explains whether buying, selling, or waiting may be appropriate.</p>
            <div v-if="lastScan" class="scan-summary" :class="{ 'scan-summary-error': scanFailed }">
              <strong>{{ scanFailed ? 'Prices are temporarily unavailable' : 'Market check complete' }}</strong>
              <span>{{ scanStatusMessage }}</span>
              <button v-if="scanFailed" class="button secondary" type="button" :disabled="busy" @click="scanMarket">Try again</button>
            </div>
          </div>
          <div class="section-body market-groups">
            <section v-for="group in groupedMarket" :key="group.label" class="market-group">
              <div class="market-group-head">
                <h3>{{ group.label }}</h3>
                <span class="muted">{{ group.rows.length }} {{ group.rows.length === 1 ? 'market' : 'markets' }}</span>
              </div>
              <div class="table-wrap market-table-wrap">
                <table class="market-table">
                  <thead>
                    <tr>
                      <th>Market</th><th>Suggestion</th><th class="num">Price</th><th class="num">ADX</th>
                      <th class="num">RSI</th><th>VWAP</th><th class="num">Stop loss</th>
                      <th class="num">Take profit</th><th class="num">Reward / risk</th><th>Guidance</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in group.rows" :key="row.symbol">
                      <td><strong class="nowrap">{{ row.symbol }}</strong><span class="market-regime">{{ row.regime }}</span></td>
                      <td><span class="signal-pill" :class="row.decision.toLowerCase()">{{ row.decision }}</span></td>
                      <td class="num nowrap">{{ row.price }}</td>
                      <td class="num">{{ row.adx }}</td>
                      <td class="num">{{ row.rsi }}</td>
                      <td>{{ row.price_vs_vwap }}</td>
                      <td class="num">{{ row.stop_loss ?? '—' }}</td>
                      <td class="num">{{ row.take_profit ?? '—' }}</td>
                      <td class="num nowrap">{{ row.risk_reward_ratio ? `${row.risk_reward_ratio}:1` : '—' }}</td>
                      <td class="market-guidance">
                        <strong class="guidance-summary">{{ row.trade_plan?.entry || row.reason }}</strong>
                        <div class="actions market-actions">
                          <button class="button secondary" :disabled="busy || row.decision === 'HOLD'" @click="quickTrade(row)">Open From Signal</button>
                          <button class="button secondary" :disabled="busy || row.decision === 'HOLD' || !liveTradingReady" @click="quickLiveTrade(row)">Send Live Order</button>
                          <button class="button secondary" type="button" :aria-expanded="expandedGuidance.has(row.symbol)" @click="toggleGuidance(row.symbol)">
                            {{ expandedGuidance.has(row.symbol) ? 'Hide' : 'View' }}
                          </button>
                        </div>
                        <div v-if="expandedGuidance.has(row.symbol)" class="guidance-details">
                          <span v-if="row.trade_plan"><b>When to place:</b> {{ row.trade_plan.when_to_place }}</span>
                          <span v-if="row.trade_plan"><b>When to stop:</b> {{ row.trade_plan.when_to_stop }}</span>
                          <span v-if="row.trade_plan"><b>When to take profit:</b> {{ row.trade_plan.when_to_take_profit }}</span>
                          <span v-if="row.trade_plan"><b>Cancel this idea:</b> {{ row.trade_plan.cancel_if }}</span>
                          <span class="muted">{{ row.reason }}</span>
                          <span v-if="row.exit_recommendation_basis" class="muted">
                            {{ row.exit_recommendation_basis }} Recommendations are informational and are not applied automatically.
                          </span>
                        </div>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
            <div v-if="!market.length" class="empty-state">
              {{ scanFailed ? 'No suggestions can be created without current prices. Try again when the provider is available.' : 'Choose markets in Setup, then select “Look for opportunities”.' }}
            </div>
          </div>
        </article>

        <article v-show="tab === 'trade'" class="card section-card">
          <div class="section-head">
            <h2>Rebalancing</h2>
            <p>Track drift versus target allocations and apply one-step corrective actions.</p>
          </div>
          <div class="section-body">
            <div class="item-card">
              <div class="item-row">
                <div class="item-title">Mode</div>
                <div class="kv">{{ rebalancePlan.key_mode || 'asset_class' }}</div>
              </div>
              <div class="item-row">
                <div class="item-title">Tolerance</div>
                <div class="kv">{{ formatPercent(rebalancePlan.tolerance || 0) }}</div>
              </div>
              <div class="item-row">
                <div class="item-title">Enabled</div>
                <div class="kv">{{ rebalancePlan.enabled ? 'Yes' : 'No' }}</div>
              </div>
            </div>

            <div class="weight-grid">
              <div class="item-card">
                <div class="item-title">Current Weights</div>
                <div v-if="Object.keys(rebalancePlan.current_weights || {}).length" class="stack-list">
                  <div
                    v-for="(value, key) in rebalancePlan.current_weights"
                    :key="`current-${key}`"
                    class="item-row"
                  >
                    <span>{{ key }}</span>
                    <span>{{ formatPercent(value) }}</span>
                  </div>
                </div>
                <div v-else class="empty-state">No current exposure.</div>
              </div>

              <div class="item-card">
                <div class="item-title">Target Weights</div>
                <div v-if="Object.keys(rebalancePlan.target_weights || {}).length" class="stack-list">
                  <div
                    v-for="(value, key) in rebalancePlan.target_weights"
                    :key="`target-${key}`"
                    class="item-row"
                  >
                    <span>{{ key }}</span>
                    <span>{{ formatPercent(value) }}</span>
                  </div>
                </div>
                <div v-else class="empty-state">No target weights configured.</div>
              </div>
            </div>

            <div class="stack-list">
              <div v-for="action in rebalancePlan.actions" :key="`${action.action}-${action.allocation_key}`" class="item-card">
                <div class="item-row">
                  <div class="item-title">{{ action.action }}</div>
                  <div class="kv">{{ action.allocation_key }}</div>
                </div>
                <div class="note">{{ action.reason }}</div>
              </div>
              <div v-if="!rebalancePlan.actions?.length" class="empty-state">
                Rebalance is within tolerance.
              </div>
            </div>
          </div>
        </article>
      </section>

      <section class="tab-section">
        <article v-show="tab === 'signals'" class="card section-card">
          <div class="section-head">
            <h2>Portfolio</h2>
            <p>Current demo positions with refreshed market prices.</p>
          </div>
          <div class="section-body stack-list">
            <div v-for="position in portfolio" :key="position.id" class="item-card">
              <div class="item-row">
                <div>
                  <div class="item-title">{{ position.symbol }}</div>
                  <div class="muted">{{ position.asset_class }} · {{ position.side }}</div>
                </div>
                <div class="signal-pill" :class="position.side.toLowerCase()">{{ position.side }}</div>
              </div>
              <div class="metric-grid">
                <div class="metric">
                  <span class="metric-label">Exposure</span>
                  <span class="metric-value">{{ formatCurrency(position.exposure) }}</span>
                </div>
                <div class="metric">
                  <span class="metric-label">Entry</span>
                  <span class="metric-value">{{ position.entry_price }}</span>
                </div>
                <div class="metric">
                  <span class="metric-label">Current</span>
                  <span class="metric-value">{{ position.current_price }}</span>
                </div>
              </div>
            </div>
            <div v-if="!portfolio.length" class="empty-state">No paper positions are open.</div>
          </div>
        </article>

        <article v-show="tab === 'backtest'" class="card section-card">
          <div class="section-head">
            <h2>Test the strategy with past market data</h2>
            <p class="beginner-intro">
              This is a practice run. It checks what might have happened if the app had used its strategy in the
              past. It does not place a real trade, and good past results do not guarantee future profit.
            </p>
            <div class="form-grid backtest-options">
              <div class="field">
                <label>How long should each practice trade stay open?</label>
                <select v-model.number="backtestOptions.horizon_bars" class="select">
                  <option :value="1">Very short</option>
                  <option :value="5">Short</option>
                  <option :value="10">Medium</option>
                  <option :value="20">Longer</option>
                </select>
                <small class="field-help">The app currently checks {{ backtestOptions.horizon_bars }} market periods before closing.</small>
              </div>
              <div class="field">
                <label>How much history should we test?</label>
                <select v-model.number="backtestOptions.candle_count" class="select">
                  <option :value="250">A little</option>
                  <option :value="500">A normal amount</option>
                  <option :value="1000">A lot</option>
                  <option :value="2500">A very large amount</option>
                </select>
                <small class="field-help">More history takes longer but gives the strategy more situations to try.</small>
              </div>
            </div>
            <details class="advanced-options">
              <summary>Advanced cost settings</summary>
              <div class="form-grid">
                <div class="field">
                  <label>Broker payout percentage</label>
                  <input v-model.number="backtestOptions.payout_pct" class="input" type="number" min="0" max="500" />
                  <small class="field-help">Leave at 0 if you do not use fixed-payout trades.</small>
                </div>
                <div class="field">
                  <label>Trading fee percentage</label>
                  <input v-model.number="backtestOptions.fee_pct" class="input" type="number" min="0" max="5" step="0.01" placeholder="Detected automatically" />
                  <small class="field-help">The amount a broker charges when buying or selling.</small>
                </div>
                <div class="field">
                  <label class="checkbox-row">
                    <input v-model="backtestOptions.use_guardian" type="checkbox" />
                    <span>Simulate Active Trade Guardian (early TP, spike grab, trailing, SL)</span>
                  </label>
                  <small class="field-help">Uses your saved Guardian thresholds to simulate early exits bar-by-bar.</small>
                </div>
              </div>
            </details>
          </div>
          <div class="section-body">
            <div v-if="lastBacktest" class="stack-list">
              <div class="item-card">
                <div class="item-row">
                  <div class="item-title">Latest practice run</div>
                  <div class="kv">{{ friendlyDateTime(lastBacktest.run_at) }}</div>
                </div>
                <div v-if="candleBacktest" class="item-row">
                  <div class="item-title">Trades the strategy tried</div>
                  <div class="kv">
                    {{ candleBacktest.trades || 0 }} practice trades · {{ formatRate(candleBacktest.win_rate) }} ended positively
                  </div>
                </div>
                <div class="item-row">
                  <div class="item-title">Safety checks passed</div>
                  <div class="kv">{{ lastBacktest.scenario_passes }} / {{ lastBacktest.scenario_total }}</div>
                </div>
                <div class="item-row">
                  <div class="item-title">Suggested portfolio changes</div>
                  <div class="kv">{{ lastBacktest.rebalance_check?.action_count || 0 }} suggestions</div>
                </div>
              </div>

              <div v-for="row in candleBacktest?.results || []" :key="`bt-${row.symbol}`" class="item-card">
                <div v-if="row.fee_pct" class="kv">
                  After estimated fees: {{ row.total_net_return_pct }}% total result ·
                  {{ formatRate(row.net_win_rate) }} of trades ended positively · largest drop {{ row.max_net_drawdown_pct }}%
                </div>
                <div class="item-row">
                  <div class="item-title">{{ row.symbol }}</div>
                  <div class="kv">{{ row.trades }} practice trades · {{ formatRate(row.win_rate) }} ended positively</div>
                </div>
                <div class="item-row">
                  <span class="muted">Overall result {{ row.total_return_pct }}% · largest drop {{ row.max_drawdown_pct }}%</span>
                  <span class="muted">Tested {{ row.candles }} past market periods</span>
                </div>
                <div v-if="row.guardian_summary" class="kv">
                  Guardian exits: {{ row.guardian_summary.early_exits }} early
                  ({{ Object.entries(row.guardian_summary.exit_reasons || {}).map(([k, v]) => k + ': ' + v).join(', ') }})
                </div>
                <div v-if="row.payout" class="item-row">
                  <span class="muted">At {{ Math.round(row.payout * 100) }}% payout: {{ row.net_per_stake >= 0 ? '+' : '' }}{{ row.net_per_stake }} per $1 staked</span>
                  <span class="muted">Needs {{ formatRate(row.break_even_win_rate) }} positive trades to avoid a loss</span>
                </div>
                <svg v-if="(row.equity_curve || []).length > 1" viewBox="0 0 240 48" preserveAspectRatio="none" style="width: 100%; height: 48px" role="img" :aria-label="`${row.symbol} equity curve`">
                  <line x1="0" x2="240" :y1="zeroLine(row.equity_curve)" :y2="zeroLine(row.equity_curve)" stroke="currentColor" stroke-opacity="0.25" stroke-dasharray="4 4" />
                  <path :d="sparkPath(row.equity_curve)" fill="none" :stroke="(row.equity_curve)[(row.equity_curve).length - 1] >= 0 ? '#22c55e' : '#ef4444'" stroke-width="2" vector-effect="non-scaling-stroke" />
                </svg>
              </div>
              <div v-for="error in candleBacktest?.errors || []" :key="`bterr-${error.symbol}`" class="item-card">
                <div class="item-row">
                  <div class="item-title">{{ error.symbol }}</div>
                  <span class="signal-pill sell">ERROR</span>
                </div>
                <div class="note">{{ error.error }}</div>
              </div>

              <div v-for="result in lastBacktest.scenario_results" :key="result.name" class="item-card">
                <div class="item-row">
                  <div class="item-title">{{ result.name }}</div>
                  <span class="signal-pill" :class="result.passed ? 'buy' : 'sell'">
                    {{ result.passed ? 'PASS' : 'FAIL' }}
                  </span>
                </div>
                <div class="item-row">
                  <span class="muted">Expected {{ result.expected }}</span>
                  <span class="muted">Actual {{ result.actual }}</span>
                </div>
                <div class="note">{{ result.reason }}</div>
              </div>
            </div>
            <div v-else class="empty-state">No practice test yet. Select your options, then choose “Test the strategy”.</div>
          </div>
        </article>
      </section>

      <section class="tab-section">
        <article v-show="tab === 'backtest'" class="card section-card">
          <div class="section-head">
          <h2>How the strategy makes decisions</h2>
          <p>These are the checks the app uses before suggesting a trade.</p>
          </div>
          <div class="section-body stack-list">
            <div v-for="rule in rules" :key="rule.id" class="item-card">
              <div class="item-title">{{ rule.name }}</div>
              <p class="note">{{ rule.purpose }}</p>
              <div class="tag-row">
                <span v-for="item in rule.inputs" :key="`${rule.id}-${item}`" class="badge subtle">{{ item }}</span>
              </div>
            </div>
          </div>
        </article>

        <article v-show="tab === 'signals'" class="card section-card recent-activity-card">
          <div class="section-head">
            <h2>Recent Activity</h2>
            <p>Practice and live activity are labelled separately. Practice exposure is virtual; live stake is broker money at risk.</p>
            <button class="button secondary" type="button" @click="exportJournal">Export CSV</button>
          </div>
          <div class="section-body activity-table-scroll">
            <table class="activity-table">
              <thead><tr><th>Activity</th><th>Date</th><th>Market</th><th>Side</th><th class="num">Amount</th><th class="num">Result</th><th>Details</th></tr></thead>
              <tbody>
                <tr v-for="entry in tradeHistory" :key="`${entry.timestamp}-${entry.symbol}-${entry.type}`" :class="activityOutcome(entry) ? `activity-${activityOutcome(entry)}` : ''">
                  <td>
                    <strong>{{ activityDisplayLabel(entry) }}</strong>
                    <span v-if="activityOutcome(entry)" class="result-badge" :class="activityOutcome(entry)">
                      {{ activityOutcome(entry) === 'profit' ? 'Closed in profit' : activityOutcome(entry) === 'loss' ? 'Closed at a loss' : 'Closed even' }}
                    </span>
                  </td>
                  <td class="nowrap muted">{{ friendlyDateTime(entry.timestamp) }}</td>
                  <td class="nowrap">{{ entry.symbol || 'App' }}</td>
                  <td>{{ entry.side || '—' }}</td>
                  <td class="num nowrap">
                    <template v-if="activityAmountLabel(entry)">
                      <strong>{{ formatCurrency(entry.exposure) }}</strong>
                      <span class="market-regime">{{ activityAmountLabel(entry) }}</span>
                    </template>
                    <span v-else>—</span>
                  </td>
                  <td class="num nowrap activity-result" :class="activityOutcome(entry)">{{ activityResult(entry) }}</td>
                  <td class="activity-note" :title="entry.note">{{ entry.note }}</td>
                </tr>
              </tbody>
            </table>
            <div v-if="!tradeHistory.length" class="empty-state">No trade history recorded yet.</div>
          </div>
        </article>

        <article v-show="tab === 'signals'" class="card section-card today-results-card">
          <div class="section-head">
            <h2>Today’s Results</h2>
            <p>Closed-trade results for today in South African time.</p>
          </div>
          <div class="section-body today-results-grid">
            <div class="today-result profit">
              <span>Money gained</span>
              <strong>+{{ formatCurrency(todayResults.gained) }}</strong>
              <small>{{ todayResults.wins }} winning {{ todayResults.wins === 1 ? 'trade' : 'trades' }}</small>
            </div>
            <div class="today-result loss">
              <span>Money lost</span>
              <strong>−{{ formatCurrency(todayResults.lost) }}</strong>
              <small>{{ todayResults.losses }} losing {{ todayResults.losses === 1 ? 'trade' : 'trades' }}</small>
            </div>
            <div class="today-net" :class="todayResults.net > 0 ? 'profit' : todayResults.net < 0 ? 'loss' : 'even'">
              <span>Net result</span>
              <strong>{{ todayResults.net > 0 ? '+' : todayResults.net < 0 ? '−' : '' }}{{ formatCurrency(Math.abs(todayResults.net)) }}</strong>
              <small>{{ todayResults.closed }} closed {{ todayResults.closed === 1 ? 'trade' : 'trades' }} today</small>
            </div>
            <p v-if="!todayResults.closed" class="empty-state">No trades with a recorded result have closed today yet.</p>
          </div>
        </article>
      </section>
      </div>
    </template>

    <ToastStack :toasts="toasts" @dismiss="dismissToast" />
    <ConfirmDialog
      :open="confirmState.open"
      :title="confirmState.title"
      :body="confirmState.body"
      :confirm-label="confirmState.confirmLabel"
      @confirm="settleConfirm(true)"
      @cancel="settleConfirm(false)"
    />
  </main>
</template>
