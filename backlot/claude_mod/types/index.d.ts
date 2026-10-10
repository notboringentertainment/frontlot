export type Opened = boolean

declare module 'claude-code' {
  interface PluginState {
    'frontlot-live': {
      opened: Opened
    }
  }
}
