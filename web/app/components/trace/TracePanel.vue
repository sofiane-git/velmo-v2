<script setup lang="ts">
const store = useChatSessionStore()

const inputGuardrail = computed(() =>
  store.currentTrace.find((e: TraceEvent) => e.type === 'input_guardrail')
)
const memoryRead = computed(() =>
  store.currentTrace.find((e: TraceEvent) => e.type === 'memory_read')
)
const routing = computed(() =>
  store.currentTrace.find((e: TraceEvent) => e.type === 'routing')
)
const toolResult = computed(() =>
  store.currentTrace.find((e: TraceEvent) => e.type === 'tool_result')
)
const outputGuardrail = computed(() =>
  store.currentTrace.find((e: TraceEvent) => e.type === 'output_guardrail')
)
const memoryWrite = computed(() =>
  store.currentTrace.find((e: TraceEvent) => e.type === 'memory_write')
)
const final = computed(() => store.currentTrace.find((e: TraceEvent) => e.type === 'final'))
</script>

<template>
  <div class="h-full overflow-y-auto space-y-3 p-2">
    <p
      v-if="store.currentTrace.length === 0"
      class="text-sm text-muted"
    >
      Envoyez un message pour voir son cheminement complet ici.
    </p>

    <StepGuardrail
      v-if="inputGuardrail"
      title="Garde-fou d'entrée"
      :payload="inputGuardrail.payload"
    />
    <StepMemory
      v-if="memoryRead"
      :read="memoryRead.payload"
    />
    <StepRouting
      v-if="routing"
      :routing="routing.payload"
      :tool-result="toolResult?.payload"
    />
    <StepGuardrail
      v-if="outputGuardrail"
      title="Garde-fou de sortie"
      :payload="outputGuardrail.payload"
    />
    <StepMemory
      v-if="memoryWrite"
      :write="memoryWrite.payload"
    />
    <UCard v-if="final">
      <template #header>
        <span class="font-semibold">Résultat final</span>
      </template>
      <p class="text-sm">
        Statut : <UBadge :color="final.payload.status === 'ok' ? 'success' : 'error'">
          {{ final.payload.status }}
        </UBadge>
        — {{ final.payload.latency_ms }} ms
      </p>
    </UCard>
  </div>
</template>
