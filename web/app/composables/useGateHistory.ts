export interface GateRun {
  id: string
  version_tag: string
  git_commit: string
  note_memory: number
  note_guardrails: number
  note_quality: number
  note_globale: number
  gate_passed: boolean
  triggered_by: string
  ran_at: string
  latency_p95_ms: number
  cost_per_conv: number
}

export function useGateHistory() {
  const config = useRuntimeConfig()
  return useFetch<GateRun[]>(`${config.public.apiBase}/mlops/gate/history`, {
    key: 'gate-history',
    default: () => []
  })
}
