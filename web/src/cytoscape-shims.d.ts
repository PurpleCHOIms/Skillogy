// Manual type shims for cytoscape plugins that lack @types packages
declare module 'cytoscape-cose-bilkent' {
  import cytoscape from 'cytoscape'
  const coseBilkent: cytoscape.Ext
  export default coseBilkent
}
