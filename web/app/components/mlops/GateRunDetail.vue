<script setup lang="ts">
const open = defineModel<boolean>('open', { default: false })
const props = defineProps<{ runId: string | null }>()

const runIdRef = toRef(props, 'runId')
const { data: cases } = useGateCases(runIdRef)

const grouped = computed(() => {
  const groups = new Map<string, GateCase[]>()
  for (const c of cases.value ?? []) {
    const list = groups.get(c.suite) ?? []
    list.push(c)
    groups.set(c.suite, list)
  }
  return groups
})
</script>

<template>
  <USlideover
    v-model:open="open"
    title="Détail du run"
  >
    <template #body>
      <p
        v-if="cases?.length === 0"
        class="text-sm text-muted"
      >
        Aucun cas trouvé pour ce run.
      </p>
      <div
        v-for="[suite, items] in grouped"
        :key="suite"
        class="mb-4"
      >
        <p class="text-xs font-medium text-muted mb-1">
          {{ suite }}
        </p>
        <div class="flex flex-wrap gap-1">
          <UBadge
            v-for="c in items"
            :key="c.case_id"
            :color="c.passed ? 'success' : 'error'"
            variant="subtle"
          >
            {{ c.case_id }}
          </UBadge>
        </div>
      </div>
    </template>
  </USlideover>
</template>
