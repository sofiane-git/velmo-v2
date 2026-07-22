export interface SuiteDonePayload {
  suite: 'memory' | 'guardrails' | 'quality'
  cases: number
  passed: number
  note: number
}

export interface GateFinalPayload {
  note_memory: number
  note_guardrails: number
  note_quality: number
  note_globale: number
  global_gate: number
  gate_passed: boolean
  block_rate: number
  false_positive_rate: number
  latency_p50_ms: number
  latency_p95_ms: number
  cost_per_conv: number
  run_id: string
  version_tag: string
}

export type GateEvent
  = | { stage: 'suite_done', payload: SuiteDonePayload }
    | { stage: 'final', payload: GateFinalPayload }

export const useMlopsGateStore = defineStore('mlopsGate', () => {
  const isRunning = ref(false)
  const progress = ref<SuiteDonePayload[]>([])
  const lastResult = ref<GateFinalPayload | null>(null)
  const error = ref<string | null>(null)

  function start() {
    isRunning.value = true
    progress.value = []
    lastResult.value = null
    error.value = null
  }

  function pushEvent(event: GateEvent) {
    if (event.stage === 'suite_done') {
      progress.value.push(event.payload)
    } else {
      lastResult.value = event.payload
      isRunning.value = false
    }
  }

  function fail(message: string) {
    error.value = message
    isRunning.value = false
  }

  return { isRunning, progress, lastResult, error, start, pushEvent, fail }
})
