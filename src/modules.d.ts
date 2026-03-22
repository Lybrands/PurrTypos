declare module 'yaml' {
  export function parse<T = unknown>(text: string): T
}

declare module 'simple-mind-map' {
  const MindMap: any
  export default MindMap
}

declare module 'simple-mind-map/src/plugins/AssociativeLine.js' {
  const AssociativeLine: any
  export default AssociativeLine
}

declare module 'simple-mind-map/src/parse/xmind.js' {
  const xmind: {
    parseXmindFile: (file: File, handleMultiCanvas?: Function) => Promise<any>
    transformXmind: (content: string, files?: any, handleMultiCanvas?: Function) => Promise<any>
    transformOldXmind: (content: string) => any
    transformToXmind: (data: any, name: string) => Promise<Blob>
  }
  export default xmind
}
