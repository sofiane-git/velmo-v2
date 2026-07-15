<script setup lang="ts">
defineProps<{
  routing: RoutingPayload
  toolResult?: ToolResultPayload
}>()

const handlerLabel: Record<string, string> = {
  tool: 'Outil métier',
  faq_rag: 'FAQ (RAG)',
  llm_libre: 'LLM libre'
}
</script>

<template>
  <UCard>
    <template #header>
      <div class="flex items-center gap-2">
        <span class="font-semibold">Routage</span>
        <UBadge variant="subtle">
          {{ handlerLabel[routing.handler] ?? routing.handler }}
        </UBadge>
      </div>
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

    <pre
      v-if="toolResult"
      class="mt-2 text-xs bg-muted/10 rounded p-2 overflow-x-auto"
    >{{
      JSON.stringify(toolResult.result, null, 2)
    }}</pre>
  </UCard>
</template>
