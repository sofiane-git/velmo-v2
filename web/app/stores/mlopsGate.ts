export type SuiteName = 'memory' | 'guardrails' | 'quality'

export interface SuiteStartPayload {
  suite: SuiteName
}

export interface CaseStartPayload {
  suite: SuiteName
  case_id: string
}

export interface CaseDonePayload {
  suite: SuiteName
  case_id: string
  passed: boolean
  score: number
}

export interface SuiteDonePayload {
  suite: SuiteName
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
  = | { stage: 'suite_start', payload: SuiteStartPayload }
    | { stage: 'case_start', payload: CaseStartPayload }
    | { stage: 'case_done', payload: CaseDonePayload }
    | { stage: 'suite_done', payload: SuiteDonePayload }
    | { stage: 'final', payload: GateFinalPayload }

export const useMlopsGateStore = defineStore('mlopsGate', () => {
  const isRunning = ref(false)
  const currentSuite = ref<SuiteName | null>(null)
  const currentCase = ref<string | null>(null)
  const caseResults = ref<CaseDonePayload[]>([])
  const progress = ref<SuiteDonePayload[]>([])
  const lastResult = ref<GateFinalPayload | null>(null)
  const error = ref<string | null>(null)

  function start() {
    isRunning.value = true
    currentSuite.value = null
    currentCase.value = null
    caseResults.value = []
    progress.value = []
    lastResult.value = null
    error.value = null
  }

  function pushEvent(event: GateEvent) {
    if (event.stage === 'suite_start') {
      currentSuite.value = event.payload.suite
      currentCase.value = null
      caseResults.value = []
    } else if (event.stage === 'case_start') {
      currentCase.value = event.payload.case_id
    } else if (event.stage === 'case_done') {
      currentCase.value = null
      caseResults.value.push(event.payload)
    } else if (event.stage === 'suite_done') {
      currentSuite.value = null
      currentCase.value = null
      caseResults.value = []
      progress.value.push(event.payload)
    } else {
      currentSuite.value = null
      currentCase.value = null
      caseResults.value = []
      lastResult.value = event.payload
      isRunning.value = false
    }
  }

  function fail(message: string) {
    error.value = message
    isRunning.value = false
  }

  return {
    isRunning,
    currentSuite,
    currentCase,
    caseResults,
    progress,
    lastResult,
    error,
    start,
    pushEvent,
    fail
  }
})
