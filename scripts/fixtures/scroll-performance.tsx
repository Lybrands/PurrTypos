import React from 'react'
import { createRoot } from 'react-dom/client'
import DelegationStatus from '../../src/components/AgentConversation/DelegationStatus'
import DiagnosticText from '../../src/components/AiDevInspector/DiagnosticText'
import '../../src/components/AgentConversation/ExecutionLog/index.scss'
const items = Array.from({length: 109}, (_, i) => ({delegationId:`d-${i}`, status:'done' as const,
  agentName:'analysis', agentTitle:`分析片段 ${i+1}`, objective:'临时性能测试资料', resultSummary:''}))
const activities = items.map(item => ({delegationId:item.delegationId, message:{role:'assistant' as const,
  content: ('## 人物与线索\n\n这是用于验证滚动性能的临时资料，不包含用户内容。\n\n').repeat(70)}}))
const diagnostic = {messages:Array.from({length:1000}, () => ({role:'user', content:'测试上下文'.repeat(100)}))}
function App() { const [count,setCount]=React.useState(0);return <>
  <button id="interaction" onClick={()=>setCount(count+1)}>响应 {count}</button>
  <div className="agent-conversation" id="conversation" style={{height:650,overflow:'auto',width:620,float:'left'}}>
    <DelegationStatus items={items as any} activities={activities as any}/>
  </div>
  <div className="ai-dev-inspector__body" style={{height:650,overflow:'auto',width:450,float:'left'}}>
    <DiagnosticText label="大型诊断输入" value={diagnostic}/>
  </div>
</>}
createRoot(document.getElementById('root')!).render(<App/>);
