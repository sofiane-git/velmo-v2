export async function sendChatMessage(userId: string, message: string): Promise<void> {
  const store = useChatSessionStore()
  const config = useRuntimeConfig()
  store.startExchange(userId, message)

  const response = await fetch(`${config.public.apiBase}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, message })
  })
  if (!response.body) return

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const lines = block.split('\n')
      const eventLine = lines.find(l => l.startsWith('event: '))
      const dataLine = lines.find(l => l.startsWith('data: '))
      if (eventLine && dataLine) {
        const type = eventLine.slice('event: '.length) as TraceEvent['type']
        const payload = JSON.parse(dataLine.slice('data: '.length))
        store.pushEvent({ type, payload } as TraceEvent)
      }
      boundary = buffer.indexOf('\n\n')
    }
  }
}
