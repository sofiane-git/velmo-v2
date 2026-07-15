<script setup lang="ts">
import { scenarios } from '~/data/scenarios'

const grouped = computed(() => {
  const groups = new Map<string, typeof scenarios>()
  for (const scenario of scenarios) {
    const key = scenario.sequenceGroup ?? `single-${scenario.id}`
    const list = groups.get(key) ?? []
    list.push(scenario)
    groups.set(key, list)
  }
  return [...groups.values()]
})

async function run(scenario: (typeof scenarios)[number], times = 1) {
  for (let i = 0; i < times; i++) {
    await sendChatMessage(scenario.userId, scenario.message)
  }
}
</script>

<template>
  <UCard>
    <template #header>
      <span class="font-semibold">Jeu de scénarios</span>
    </template>

    <div class="space-y-3">
      <div
        v-for="(group, gi) in grouped"
        :key="gi"
        class="rounded border border-default p-2 space-y-2"
      >
        <div
          v-for="scenario in group"
          :key="scenario.id"
          class="space-y-1"
        >
          <p class="text-xs font-medium text-muted">
            {{ scenario.category }}
          </p>
          <div class="flex items-center gap-2">
            <UButton
              size="xs"
              variant="subtle"
              :label="scenario.message"
              class="flex-1 justify-start truncate"
              @click="run(scenario)"
            />
            <UButton
              v-if="scenario.replayCount"
              size="xs"
              color="warning"
              variant="soft"
              :label="`rejouer ${scenario.replayCount}×`"
              @click="run(scenario, scenario.replayCount)"
            />
          </div>
        </div>
      </div>
    </div>
  </UCard>
</template>
