export interface CustomerOption {
  id: string
  full_name: string
}

export function useCustomers() {
  const config = useRuntimeConfig()
  return useFetch<CustomerOption[]>(`${config.public.apiBase}/customers`, {
    key: 'customers',
    default: () => []
  })
}
