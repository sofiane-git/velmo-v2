export interface GateCase {
  case_id: string
  suite: string
  passed: boolean
  score: number
  latency_ms: number
}

export function useGateCases(runId: Ref<string | null>) {
  const config = useRuntimeConfig()
  return useFetch<GateCase[]>(
    () => `${config.public.apiBase}/mlops/gate/runs/${runId.value}/cases`,
    {
      key: () => `gate-cases-${runId.value}`,
      default: () => [],
      immediate: false,
      watch: [runId]
    }
  )
}
