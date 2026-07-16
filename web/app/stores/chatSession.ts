export interface GuardrailHit {
  category: string
  method: string
  action: string
  score: number | null
  reasoning: string | null
}

export interface GuardrailEventPayload {
  hits: GuardrailHit[]
  allowed: boolean
  escalate: boolean
}

export interface FactMatch {
  key: string
  value: string
  confidence: number
}

export interface MemoryReadPayload {
  history_turns: number
  summary_used: boolean
  facts_matched: FactMatch[]
  episodic_matched: string[]
}

export interface RoutingPayload {
  handler: 'tool' | 'faq_rag' | 'llm_libre'
  detail: {
    tool_name: string | null
    order_id: string | null
    query: string | null
  }
}

export interface ToolResultPayload {
  name: string | null
  result: Record<string, unknown>
}

export interface WrittenFact {
  key: string
  value: string
  type: string
  confidence: number
}

export interface WrittenProcedure {
  trigger: string
  rule: string
  confidence: number
}

export interface MemoryWritePayload {
  facts_written: WrittenFact[]
  procedures_written: WrittenProcedure[]
  episode_created: boolean
  pending: boolean
}

export interface FinalPayload {
  answer: string
  status: 'ok' | 'blocked_input' | 'blocked_output'
  latency_ms: number
}

export type TraceEvent
  = | { type: 'input_guardrail', payload: GuardrailEventPayload }
    | { type: 'memory_read', payload: MemoryReadPayload }
    | { type: 'routing', payload: RoutingPayload }
    | { type: 'tool_result', payload: ToolResultPayload }
    | { type: 'output_guardrail', payload: GuardrailEventPayload }
    | { type: 'memory_write', payload: MemoryWritePayload }
    | { type: 'final', payload: FinalPayload }

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface SessionEntry {
  userId: string
  message: string
  events: TraceEvent[]
}

export const useChatSessionStore = defineStore('chatSession', () => {
  const messages = ref<ChatMessage[]>([])
  const currentTrace = ref<TraceEvent[]>([])
  const sessionLog = ref<SessionEntry[]>([])
  const isStreaming = ref(false)

  function startExchange(userId: string, message: string) {
    messages.value.push({ role: 'user', content: message })
    currentTrace.value = []
    isStreaming.value = true
    sessionLog.value.push({ userId, message, events: [] })
  }

  function pushEvent(event: TraceEvent) {
    currentTrace.value.push(event)
    const last = sessionLog.value[sessionLog.value.length - 1]
    if (last) last.events.push(event)
    if (event.type === 'final') {
      messages.value.push({ role: 'assistant', content: event.payload.answer })
      isStreaming.value = false
    }
  }

  return { messages, currentTrace, sessionLog, isStreaming, startExchange, pushEvent }
})
