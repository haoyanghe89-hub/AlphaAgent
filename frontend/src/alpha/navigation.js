import { onUnmounted, ref } from 'vue'

export function parseRoute(hash) {
  const path = (hash || '#/').replace(/^#/, '')
  if (path === '/') return { page: 'workbench' }
  if (['/library', '/backtest', '/compare', '/strategy', '/delivery'].includes(path)) return { page: path.slice(1) }
  const match = path.match(/^\/(research|report|stock|legacy|backtest|live|compare|strategy)\/([^/]+)$/)
  if (match) {
    try { return { page: match[1], id: decodeURIComponent(match[2]) } }
    catch { return { page: 'missing' } }
  }
  return { page: 'missing' }
}

export const href = (page, id) => '#/' + (page === 'workbench' ? '' : page + (id ? '/' + encodeURIComponent(id) : ''))

export function useNavigation() {
  const route = ref(parseRoute(window.location.hash))
  function sync() { route.value = parseRoute(window.location.hash); window.scrollTo(0, 0) }
  window.addEventListener('hashchange', sync)
  onUnmounted(() => window.removeEventListener('hashchange', sync))
  return { route, go: (page, id) => { window.location.hash = href(page, id) } }
}
