function activateNotifications(){
  if(!('Notification' in window)){
    alert('Este navegador não oferece notificações.');
    return;
  }
  Notification.requestPermission().then(permission=>{
    alert(permission==='granted'
      ? 'Notificações ativadas. Mantenha o TITAN aberto para receber os alertas.'
      : 'Permissão não concedida.');
  });
}

(function guidedNavigation(){
  const body=document.body;
  const sidebar=document.getElementById('primary-navigation');
  const openButtons=[...document.querySelectorAll('[data-nav-open]')];
  const closeButtons=[...document.querySelectorAll('[data-nav-close]')];
  if(!sidebar)return;

  function setOpen(isOpen){
    body.classList.toggle('nav-open',isOpen);
    openButtons.forEach(button=>button.setAttribute('aria-expanded',String(isOpen)));
    if(isOpen){
      const active=sidebar.querySelector('.nav-item.active');
      (active||sidebar.querySelector('a,button'))?.focus({preventScroll:true});
    }
  }

  openButtons.forEach(button=>button.addEventListener('click',()=>setOpen(true)));
  closeButtons.forEach(button=>button.addEventListener('click',()=>setOpen(false)));
  sidebar.querySelectorAll('a').forEach(link=>link.addEventListener('click',()=>setOpen(false)));
  document.addEventListener('keydown',event=>{if(event.key==='Escape')setOpen(false);});
  window.addEventListener('resize',()=>{if(window.innerWidth>900)setOpen(false);});
})();

(function mealReminders(){
  const reminders=window.TITAN_REMINDERS||[];
  if(!reminders.length)return;
  setInterval(()=>{
    const now=new Date();
    const hh=String(now.getHours()).padStart(2,'0')+':'+String(now.getMinutes()).padStart(2,'0');
    reminders.forEach(reminder=>{
      const key='titan-'+reminder.id+'-'+now.toDateString();
      if(reminder.time===hh&&Notification.permission==='granted'&&!sessionStorage.getItem(key)){
        new Notification('Projeto TITAN',{body:reminder.title});
        sessionStorage.setItem(key,'1');
      }
    });
  },30000);
})();

(function foodSearch(){
  const input=document.querySelector('[data-food-search]');
  const grid=document.querySelector('[data-food-grid]');
  const empty=document.querySelector('[data-food-empty]');
  if(!input||!grid)return;
  const cards=[...grid.querySelectorAll('[data-food-name]')];
  const normalize=value=>value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase().trim();
  input.addEventListener('input',()=>{
    const query=normalize(input.value);
    let visible=0;
    cards.forEach(card=>{
      const show=normalize(card.dataset.foodName).includes(query);
      card.hidden=!show;
      if(show)visible+=1;
    });
    if(empty)empty.hidden=visible!==0;
  });
})();

(function exerciseCatalog(){
  const browser=document.querySelector('[data-exercise-catalog]');
  if(!browser)return;

  const input=browser.querySelector('[data-catalog-search]');
  const clearButton=browser.querySelector('[data-catalog-clear]');
  const cards=[...browser.querySelectorAll('[data-catalog-card]')];
  const filterButtons=[...browser.querySelectorAll('[data-catalog-filter]')];
  const resetButtons=[...browser.querySelectorAll('[data-catalog-reset]')];
  const count=browser.querySelector('[data-catalog-count]');
  const empty=browser.querySelector('[data-catalog-empty]');
  const formQueries=[...browser.querySelectorAll('[data-catalog-form-query]')];
  const formGroups=[...browser.querySelectorAll('[data-catalog-form-group]')];
  const normalize=value=>(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase().trim();
  let selectedGroup=filterButtons.find(button=>button.classList.contains('active'))?.dataset.catalogFilter||'';

  function updateUrl(query){
    const url=new URL(window.location.href);
    query ? url.searchParams.set('q',query) : url.searchParams.delete('q');
    selectedGroup ? url.searchParams.set('group',selectedGroup) : url.searchParams.delete('group');
    window.history.replaceState({},'',url.pathname+url.search+url.hash);
  }

  function applyFilters(){
    const rawQuery=input?.value.trim()||'';
    const query=normalize(rawQuery);
    let visible=0;
    cards.forEach(card=>{
      const matchesText=!query||normalize(card.dataset.catalogText).includes(query);
      const matchesGroup=!selectedGroup||card.dataset.catalogGroup===selectedGroup;
      const show=matchesText&&matchesGroup;
      card.hidden=!show;
      if(show)visible+=1;
    });
    if(count)count.textContent=String(visible);
    if(empty)empty.hidden=visible!==0;
    if(clearButton)clearButton.hidden=!rawQuery;
    formQueries.forEach(field=>{field.value=rawQuery;});
    formGroups.forEach(field=>{field.value=selectedGroup;});
    updateUrl(rawQuery);
  }

  function chooseGroup(group){
    selectedGroup=group;
    filterButtons.forEach(button=>{
      const active=button.dataset.catalogFilter===selectedGroup;
      button.classList.toggle('active',active);
      button.setAttribute('aria-pressed',String(active));
    });
    applyFilters();
  }

  input?.addEventListener('input',applyFilters);
  clearButton?.addEventListener('click',()=>{
    input.value='';
    input.focus();
    applyFilters();
  });
  filterButtons.forEach(button=>button.addEventListener('click',()=>chooseGroup(button.dataset.catalogFilter||'')));
  resetButtons.forEach(button=>button.addEventListener('click',()=>{
    if(input)input.value='';
    chooseGroup('');
    input?.focus();
  }));
  applyFilters();
})();
