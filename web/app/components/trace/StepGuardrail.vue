<script setup lang="ts">
defineProps<{
  title: string
  subtitle: string
  step: number
  payload: GuardrailEventPayload
}>()

function actionColor(action: string) {
  if (action === 'block') return 'error'
  if (action === 'flag') return 'warning'
  return 'success'
}

const methodLabel: Record<string, string> = {
  llama_guard: 'Llama Guard 3 (sémantique)',
  lexical: 'lexique (mots-clés)',
  judge: 'juge LLM'
}

function labelOf(method: string) {
  return methodLabel[method] ?? method
}
</script>

<template>
  <UCard>
    <template #header>
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2">
          <UBadge
            variant="subtle"
            color="neutral"
          >
            {{ step }}
          </UBadge>
          <span class="font-semibold">{{ title }}</span>
        </div>
        <div class="flex gap-2">
          <UBadge :color="payload.allowed ? 'success' : 'error'">
            {{ payload.allowed ? 'allow' : 'block' }}
          </UBadge>
          <UBadge
            v-if="payload.escalate"
            color="warning"
          >
            escalade
          </UBadge>
        </div>
      </div>
      <p class="mt-1 text-xs text-muted">
        {{ subtitle }}
      </p>
    </template>

    <p
      v-if="payload.hits.length === 0"
      class="text-sm text-muted"
    >
      Aucun garde-fou déclenché — le contenu a passé tous les filtres sans alerte.
    </p>

    <ul
      v-else
      class="space-y-2"
    >
      <li
        v-for="(hit, i) in payload.hits"
        :key="i"
        class="text-sm"
      >
        <div class="flex items-center gap-2">
          <UBadge
            :color="actionColor(hit.action)"
            variant="subtle"
          >
            {{ hit.category }}
          </UBadge>
          <span class="text-muted">{{ labelOf(hit.method) }}</span>
          <span
            v-if="hit.score !== null"
            class="text-muted"
          >score={{ hit.score }}</span>
        </div>
        <p
          v-if="hit.reasoning"
          class="mt-1 italic text-muted"
        >
          {{ hit.reasoning }}
        </p>
      </li>
    </ul>
  </UCard>
</template>
