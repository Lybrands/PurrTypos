import React from 'react'
import '../styles/purr.scss'

export interface PurrFormRule {
  required?: boolean
  message?: string
  type?: 'number'
  min?: number
  max?: number
}

export interface PurrFormInstance<Values extends Record<string, any> = Record<string, any>> {
  getFieldValue: <K extends keyof Values>(name: K) => Values[K]
  setFieldValue: <K extends keyof Values>(name: K, value: Values[K]) => void
  setFieldsValue: (values: Partial<Values>) => void
  resetFields: () => void
  validateFields: () => Promise<Values>
  __subscribe: (listener: () => void) => () => void
  __register: (name: string, rules?: PurrFormRule[]) => () => void
  __getError: (name: string) => string | undefined
}

function createForm<Values extends Record<string, any>>(): PurrFormInstance<Values> {
  let values = {} as Values
  let initialValues = {} as Values
  const listeners = new Set<() => void>()
  const rules = new Map<string, PurrFormRule[]>()
  const errors = new Map<string, string>()
  const notify = () => listeners.forEach((listener) => listener())

  return {
    getFieldValue: (name) => values[name],
    setFieldValue: (name, value) => {
      values = { ...values, [name]: value }
      errors.delete(String(name))
      notify()
    },
    setFieldsValue: (nextValues) => {
      values = { ...values, ...nextValues }
      initialValues = { ...initialValues, ...nextValues }
      errors.clear()
      notify()
    },
    resetFields: () => {
      values = { ...initialValues }
      errors.clear()
      notify()
    },
    validateFields: async () => {
      errors.clear()
      rules.forEach((fieldRules, name) => {
        const value = values[name]
        for (const rule of fieldRules) {
          if (rule.required && (value == null || value === '')) {
            errors.set(name, rule.message ?? '此项为必填项')
            break
          }
          if (rule.type === 'number' && value != null) {
            const number = Number(value)
            if (Number.isNaN(number) || (rule.min != null && number < rule.min) || (rule.max != null && number > rule.max)) {
              errors.set(name, rule.message ?? '数值超出范围')
              break
            }
          }
        }
      })
      notify()
      if (errors.size) return Promise.reject(new Error('表单校验失败'))
      return { ...values }
    },
    __subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    __register: (name, fieldRules = []) => {
      rules.set(name, fieldRules)
      return () => rules.delete(name)
    },
    __getError: (name) => errors.get(name),
  }
}

const FormContext = React.createContext<PurrFormInstance<any> | null>(null)

interface FormProps<Values extends Record<string, any> = Record<string, any>> {
  form?: PurrFormInstance<Values>
  children?: React.ReactNode
  layout?: 'horizontal' | 'vertical' | 'inline'
  className?: string
  style?: React.CSSProperties
}

function FormBase<Values extends Record<string, any>>({
  form,
  children,
  layout = 'horizontal',
  className,
  style,
}: FormProps<Values>) {
  const internalRef = React.useRef<PurrFormInstance<Values>>()
  if (!internalRef.current) internalRef.current = createForm<Values>()
  const resolved = form ?? internalRef.current
  return (
    <FormContext.Provider value={resolved}>
      <form className={['purr-form', `purr-form--${layout}`, className].filter(Boolean).join(' ')} style={style} onSubmit={(event) => event.preventDefault()}>
        {children}
      </form>
    </FormContext.Provider>
  )
}

interface FormItemProps {
  name?: string
  label?: React.ReactNode
  children?: React.ReactNode
  required?: boolean
  rules?: PurrFormRule[]
  valuePropName?: string
  extra?: React.ReactNode
  className?: string
}

function FormItem({ name, label, children, required, rules, valuePropName = 'value', extra, className }: FormItemProps) {
  const form = React.useContext(FormContext)
  const [, forceRender] = React.useReducer((value) => value + 1, 0)

  React.useEffect(() => {
    if (!form) return
    return form.__subscribe(forceRender)
  }, [form])
  React.useEffect(() => {
    if (!form || !name) return
    return form.__register(name, rules)
  }, [form, name, rules])

  let control = children
  if (name && form && React.isValidElement(children)) {
    const child = children as React.ReactElement<Record<string, any>>
    const originalOnChange = child.props.onChange
    control = React.cloneElement(child, {
      [valuePropName]: form.getFieldValue(name),
      onChange: (...args: any[]) => {
        originalOnChange?.(...args)
        const first = args[0]
        const nextValue = first?.target ? first.target[valuePropName] : first
        form.setFieldValue(name, nextValue)
      },
    })
  }

  const error = name && form ? form.__getError(name) : undefined
  return (
    <div className={['purr-form-item', error && 'purr-form-item--error', className].filter(Boolean).join(' ')}>
      {label != null && <label className="purr-form-item__label">{label}{(required || rules?.some((rule) => rule.required)) && <span aria-hidden> *</span>}</label>}
      <div className="purr-form-item__control">
        {control}
        {extra != null && <div className="purr-form-item__extra">{extra}</div>}
        {error && <div className="purr-form-item__error">{error}</div>}
      </div>
    </div>
  )
}

function useForm<Values extends Record<string, any> = Record<string, any>>(): [PurrFormInstance<Values>] {
  const ref = React.useRef<PurrFormInstance<Values>>()
  if (!ref.current) ref.current = createForm<Values>()
  return [ref.current]
}

function useWatch<Values extends Record<string, any>, K extends keyof Values>(
  name: K,
  form: PurrFormInstance<Values>,
): Values[K] {
  const [, forceRender] = React.useReducer((value) => value + 1, 0)
  React.useEffect(() => form.__subscribe(forceRender), [form])
  return form.getFieldValue(name)
}

type FormComponent = typeof FormBase & {
  Item: typeof FormItem
  useForm: typeof useForm
  useWatch: typeof useWatch
}

export const PurrForm = FormBase as FormComponent
PurrForm.Item = FormItem
PurrForm.useForm = useForm
PurrForm.useWatch = useWatch
