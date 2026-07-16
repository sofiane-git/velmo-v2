<script setup lang="ts">
const props = defineProps<{
  step: number
  routing: RoutingPayload
  toolResult?: ToolResultPayload
}>()

const handlerLabel: Record<string, string> = {
  tool: 'Outil métier',
  faq_rag: 'FAQ (RAG)',
  llm_libre: 'LLM libre'
}

const handlerExplain: Record<string, string> = {
  tool: 'La demande correspond à une action métier connue (ex : suivi de commande) → appel direct à un outil.',
  faq_rag: 'La demande ressemble à une question générale → recherche dans la base de connaissances (RAG).',
  llm_libre: 'Aucun outil ni FAQ ne correspond → réponse générée librement par le LLM.'
}

const forgetResult = computed<ForgetResult | null>(() => {
  const name = props.toolResult?.name
  if (name !== 'memory_forget' && name !== 'memory_forget_all') return null
  return props.toolResult?.result as unknown as ForgetResult
})
</script>

<template>
  <UCard>
    <template #header>
      <div class="flex items-center gap-2">
        <UBadge
          variant="subtle"
          color="neutral"
        >
          {{ step }}
        </UBadge>
        <span class="font-semibold">Routage</span>
        <UBadge variant="subtle">
          {{ handlerLabel[routing.handler] ?? routing.handler }}
        </UBadge>
      </div>
      <p class="mt-1 text-xs text-muted">
        {{ handlerExplain[routing.handler] ?? "Décide quelle stratégie traite la demande." }}
      </p>
    </template>

    <ul class="text-sm space-y-1">
      <li v-if="routing.detail.tool_name">
        Outil : {{ routing.detail.tool_name }}
      </li>
      <li v-if="routing.detail.order_id">
        Commande : {{ routing.detail.order_id }}
      </li>
      <li v-if="routing.detail.query">
        Requête : {{ routing.detail.query }}
      </li>
    </ul>

    <div
      v-if="forgetResult"
      class="mt-2 text-sm"
    >
      <p
        v-if="forgetResult.removed === 0"
        class="text-muted"
      >
        Rien trouvé à supprimer.
      </p>
      <ul
        v-else
        class="space-y-1"
      >
        <li
          v-for="f in forgetResult.facts"
          :key="`fact-${f.key}`"
        >
          Fait oublié : <UBadge variant="subtle">
            {{ f.key }}
          </UBadge> = {{ f.value }}
        </li>
        <li
          v-for="p in forgetResult.procedures"
          :key="`proc-${p.trigger}`"
        >
          Procédure oubliée : {{ p.trigger }} → {{ p.rule }}
        </li>
        <li
          v-for="(e, i) in forgetResult.episodes"
          :key="`epi-${i}`"
        >
          Épisode oublié : {{ e }}
        </li>
      </ul>
    </div>

    <pre
      v-else-if="toolResult"
      class="mt-2 text-xs bg-muted/10 rounded p-2 overflow-x-auto"
    >{{
      JSON.stringify(toolResult.result, null, 2)
    }}</pre>
  </UCard>
</template>
