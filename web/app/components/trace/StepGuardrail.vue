<script setup lang="ts">
defineProps<{
  title: string
  payload: GuardrailEventPayload
}>()

function actionColor(action: string) {
  if (action === 'block') return 'error'
  if (action === 'flag') return 'warning'
  return 'success'
}
</script>

<template>
  <UCard>
    <template #header>
      <div class="flex items-center justify-between">
        <span class="font-semibold">{{ title }}</span>
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
    </template>

    <p
      v-if="payload.hits.length === 0"
      class="text-sm text-muted"
    >
      Aucun garde-fou déclenché.
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
          <span class="text-muted">{{ hit.method }}</span>
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
