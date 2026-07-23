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
    <PipelineFlowAnimation v-if="store.isStreaming && store.currentTrace.length === 0" />

    <div
      v-else-if="store.currentTrace.length === 0"
      class="text-sm text-muted space-y-2"
    >
      <p>Envoyez un message pour voir son cheminement complet ici.</p>
      <p class="text-xs">
        Ce panneau retrace, étape par étape, tout ce que l'agent fait en coulisses :
        vérification du message (garde-fou d'entrée), lecture de la mémoire de
        l'utilisateur, choix de la stratégie de réponse (routage), vérification de
        la réponse (garde-fou de sortie), puis mise à jour de la mémoire.
      </p>
    </div>

    <StepGuardrail
      v-if="inputGuardrail"
      :step="1"
      title="Garde-fou d'entrée"
      subtitle="Analyse le message reçu avant qu'il n'atteigne l'agent : injection de prompt, toxicité, contenu hors périmètre."
      :payload="inputGuardrail.payload"
    />
    <StepMemory
      v-if="memoryRead"
      :step="2"
      :read="memoryRead.payload"
    />
    <StepRouting
      v-if="routing"
      :step="3"
      :routing="routing.payload"
      :tool-result="toolResult?.payload"
    />
    <StepGuardrail
      v-if="outputGuardrail"
      :step="4"
      title="Garde-fou de sortie"
      subtitle="Relit la réponse générée avant envoi : cohérence, ton, absence de contenu sensible."
      :payload="outputGuardrail.payload"
    />
    <StepMemory
      v-if="memoryWrite"
      :step="5"
      :write="memoryWrite.payload"
    />
    <UCard v-if="final">
      <template #header>
        <div class="flex items-center gap-2">
          <UBadge
            variant="subtle"
            color="neutral"
          >
            6
          </UBadge>
          <span class="font-semibold">Résultat final</span>
        </div>
        <p class="mt-1 text-xs text-muted">
          Bilan du tour : statut global et temps de bout en bout.
        </p>
      </template>
      <p class="text-sm">
        Statut : <UBadge :color="final.payload.status === 'ok' ? 'success' : 'error'">
          {{ final.payload.status }}
        </UBadge>
        — {{ formatDuration(final.payload.latency_ms) }}
      </p>
      <p class="mt-1 text-xs text-muted">
        Ce délai couvre le tour complet, du clic sur « Envoyer » à la réponse
        finale : garde-fou d'entrée, lecture mémoire, routage, génération de
        la réponse, garde-fou de sortie et écriture mémoire.
      </p>
      <p
        v-if="final.payload.status !== 'ok'"
        class="mt-1 text-xs text-muted"
      >
        {{ final.payload.status === 'blocked_input'
          ? "Bloqué au garde-fou d'entrée : la réponse n'a jamais été générée."
          : "Bloqué au garde-fou de sortie : la réponse générée a été retenue avant envoi." }}
      </p>
    </UCard>
  </div>
</template>
