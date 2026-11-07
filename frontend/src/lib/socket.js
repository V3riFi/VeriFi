const MAX_RETRY_MS = 5000

// Keeps one WebSocket open, reconnecting with backoff when it drops.
export function connectSocket({ url, onMessage, onStatus }) {
  let ws = null
  let retryMs = 500

  function open() {
    onStatus('connecting')
    ws = new WebSocket(url)

    ws.onopen = () => {
      retryMs = 500
      onStatus('open')
    }

    ws.onmessage = (event) => {
      let message
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      if (message && typeof message === 'object') onMessage(message)
    }

    ws.onclose = () => {
      onStatus('closed')
      setTimeout(open, retryMs)
      retryMs = Math.min(retryMs * 2, MAX_RETRY_MS)
    }
  }

  open()

  return {
    send(message) {
      if (ws?.readyState !== WebSocket.OPEN) return false
      ws.send(JSON.stringify(message))
      return true
    },
  }
}
