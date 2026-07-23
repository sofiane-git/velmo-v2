<script setup lang="ts">
const open = defineModel<boolean>('open', { default: false })
const store = useChatSessionStore()

function statusOf(entry: (typeof store.sessionLog)[number]) {
  const final = entry.events.find((e: TraceEvent) => e.type === 'final')
  return final?.type === 'final' ? final.payload.status : 'ok'
}

function guardrailSummary(entry: (typeof store.sessionLog)[number]) {
  const hits = entry.events
    .filter((e: TraceEvent) => e.type === 'input_guardrail' || e.type === 'output_guardrail')
    .flatMap((e: TraceEvent) =>
      e.type === 'input_guardrail' || e.type === 'output_guardrail' ? e.payload.hits : []
    )
  return hits.map((h: GuardrailHit) => h.category)
}
</script>

<template>
  <USlideover
    v-model:open="open"
    title="Bilan de session"
  >
    <template #body>
      <div class="space-y-4">
        <p
          v-if="store.sessionLog.length === 0"
          class="text-sm text-muted"
        >
          Aucun échange dans cette session pour l'instant.
        </p>
        <UCard
          v-for="(entry, i) in store.sessionLog"
          :key="i"
        >
          <template #header>
            <div class="flex items-center justify-between">
              <span class="text-sm font-medium">{{ entry.userId }}</span>
              <UBadge :color="statusOf(entry) === 'ok' ? 'success' : 'error'">
                {{ statusOf(entry) }}
              </UBadge>
            </div>
          </template>
          <p class="text-sm">
            {{ entry.message }}
          </p>
          <div
            v-if="guardrailSummary(entry).length"
            class="mt-2 flex gap-1 flex-wrap"
          >
            <UBadge
              v-for="(c, ci) in guardrailSummary(entry)"
              :key="ci"
              variant="subtle"
            >
              {{ c }}
            </UBadge>
          </div>
        </UCard>
      </div>
    </template>
  </USlideover>
</template>
