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
