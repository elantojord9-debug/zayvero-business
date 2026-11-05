"""Sandbox actions on a saved sale, reached from the POS receipt."""

def panel(sale_id):
    return '''
<section class="card no-print" id="sandbox-panel">
<h3>Prueba de facturación electrónica</h3>
<p>Sandbox DGII · Sin valor fiscal. E31 usa el receptor de prueba GRUPO VILLAR SRL.</p>
<label for="sandbox-type">Tipo de prueba</label>
<select id="sandbox-type"><option value="32">E32 Consumo</option><option value="31">E31 Crédito fiscal</option></select>
<button type="button" class="btn" data-sandbox="emitir">Enviar prueba</button>
<button type="button" class="btn ghost" data-sandbox="consultar">Consultar resultado</button>
<p id="sandbox-status" role="status" aria-live="polite"></p>
<a id="sandbox-qr" target="_blank" rel="noopener noreferrer" hidden>Consultar QR en DGII de pruebas</a>
<button type="button" id="sandbox-xml" hidden>Descargar XML de prueba</button>
</section>
<script>
(()=>{
  let xml='', ncf='';
  const box=document.getElementById('sandbox-panel');
  const status=document.getElementById('sandbox-status');
  const buttons=box.querySelectorAll('[data-sandbox]');
  buttons.forEach(button=>button.addEventListener('click',async()=>{
    buttons.forEach(b=>b.disabled=true);
    status.textContent='Consultando el Sandbox…';
    try {
      const response=await fetch('/api/pos/sandbox',{
        method:'POST',headers:{'Content-Type':'application/json',
          'X-CSRF-Token':document.querySelector('meta[name=csrf-token]').content},
        body:JSON.stringify({venta_id:SALE_ID,tipo:document.getElementById('sandbox-type').value,accion:button.dataset.sandbox})
      });
      const data=await response.json();
      if(!response.ok || !data.ok) throw new Error(data.error || 'Consulta pendiente');
      ncf=data.ncf; xml=data.xml;
      status.textContent=ncf+' · '+data.estado+(data.mensaje?' · '+data.mensaje:'');
      const qr=document.getElementById('sandbox-qr');
      qr.hidden=!data.qr;
      if(data.qr) qr.href=data.qr;
      document.getElementById('sandbox-xml').hidden=!xml;
    } catch(error){status.textContent='Pendiente de confirmar. Pulse Consultar resultado. '+error.message;}
    finally{buttons.forEach(b=>b.disabled=false);}
  }));
  document.getElementById('sandbox-xml').onclick=()=>{
    const url=URL.createObjectURL(new Blob([xml],{type:'application/xml'}));
    const link=document.createElement('a'); link.href=url; link.download=ncf+'-sandbox.xml';link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
})();
</script>
'''.replace('SALE_ID', str(int(sale_id)))
