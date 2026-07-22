export async function runGate(): Promise<void> {
  const store = useMlopsGateStore()
  const config = useRuntimeConfig()
  store.start()

  const response = await fetch(`${config.public.apiBase}/mlops/gate/run`, { method: 'POST' })
  if (!response.ok) {
    store.fail(`Le gate n'a pas pu démarrer (HTTP ${response.status}).`)
    return
  }
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
        const stage = eventLine.slice('event: '.length) as GateEvent['stage']
        const payload = JSON.parse(dataLine.slice('data: '.length))
        store.pushEvent({ stage, payload } as GateEvent)
      }
      boundary = buffer.indexOf('\n\n')
    }
  }
}
