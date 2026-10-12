import { nextTick, onMounted, onUnmounted } from 'vue'

export function useEvidencePopover(dialog, close) {
  const anchor = document.activeElement?.getBoundingClientRect()
  let observer, disposed = false
  onMounted(async () => {
    await nextTick()
    const node = dialog.value
    if (!node || disposed) return
    node.showModal()
    function position() {
      const width = node.getBoundingClientRect().width, height = node.getBoundingClientRect().height
      node.style.left = Math.max(8, Math.min(anchor?.left || 8, innerWidth - width - 8)) + 'px'
      const below = (anchor?.bottom || 8) + 8
      node.style.top = (below + height <= innerHeight - 8 ? below : Math.max(8, (anchor?.top || 8) - height - 8)) + 'px'
    }
    position(); observer = new ResizeObserver(position); observer.observe(node)
  })
  onUnmounted(() => { disposed = true; observer?.disconnect() })
  function outside(event) {
    const rect = dialog.value.getBoundingClientRect()
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close()
  }
  return outside
}
