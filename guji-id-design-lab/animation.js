(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const story = $('.story');
  const root = $('#story-sticky');
  const book = $('#book-photo');
  const macro = $('#manuscript-photo');
  const beam = $('#light-beam');
  const curtain = $('#paper-curtain');
  const hero = $('#hero-content');
  const macroCaption = $('#macro-caption');
  const focus = $('#ink-focus');
  const paperIntro = $('#paper-intro');
  const railCurrent = $('#rail-current');
  const railFill = $('#rail-progress');
  const stageName = $('#stage-name');
  const topbar = $('#topbar');
  const scrollIndicator = $('#scroll-indicator');
  const stageCaption = $('#stage-caption');
  const menuButton = $('#menu-toggle');
  const menu = $('#main-nav');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const clamp = (v, min=0, max=1) => Math.min(max, Math.max(min,v));
  const span = (x, start, end) => clamp((x - start)/(end - start));
  const smooth = t => t * t * (3 - 2*t);
  const interp = (a,b,t) => a+(b-a)*t;
  let requestId=0;
  let progress=0;
  let stage=1;

  function updateScene() {
    requestId=0;
    const rect = story.getBoundingClientRect();
    const distance = Math.max(1, story.offsetHeight - window.innerHeight);
    progress = clamp(-rect.top / distance);
    const reduced = reduceMotion.matches;
    if (reduced) {
      book.style.transform = 'translate3d(0,0,0) scale(1)';
      book.style.opacity = progress < .43 ? '1':'0';
      macro.style.transform = 'translate3d(0,0,0) scale(1)';
    } else {
      const bookZoom=smooth(span(progress,.08,.60));
      book.style.transform=`translate3d(${interp(0,-6,bookZoom)}%, ${interp(0,-7,bookZoom)}%, 0) scale(${interp(1,1.75,bookZoom)})`;
      macro.style.transform=`translate3d(0, ${interp(3,-5,smooth(span(progress,.29,.73)))}%, 0) scale(${interp(1.07,1.28,smooth(span(progress,.29,.73)))})`;
    }
    book.style.opacity = String(reduced ? (progress<.43?1:0) : (1-smooth(span(progress,.38,.64))));
    macro.style.opacity = String(smooth(span(progress,.28,.52)) * (1-smooth(span(progress,.70,.91))));
    beam.style.opacity = String((1-smooth(span(progress,.15,.56)))*.8);
    curtain.style.opacity=String(smooth(span(progress,.65,.91)));
    hero.style.opacity=String(1-smooth(span(progress,.14,.36)));
    hero.style.transform = reduced ? '' : `translate3d(0,${interp(0,-44,smooth(span(progress,.08,.38)))}px,0)`;
    hero.style.pointerEvents=progress>.32?'none':'auto';
    macroCaption.style.opacity=String(smooth(span(progress,.33,.50))*(1-smooth(span(progress,.67,.81))));
    macroCaption.style.transform = reduced?'':`translate3d(0,${interp(25,0,smooth(span(progress,.33,.52)))}px,0)`;
    focus.style.opacity=String(smooth(span(progress,.46,.58))*(1-smooth(span(progress,.68,.79))));
    paperIntro.style.opacity=String(smooth(span(progress,.82,.98)));
    paperIntro.style.transform=reduced?'':`translate3d(0,${interp(35,0,smooth(span(progress,.82,.98)))}px,0)`;
    paperIntro.style.pointerEvents=progress>.83?'auto':'none';
    scrollIndicator.style.opacity=String(1-smooth(span(progress,.02,.19)));
    stageCaption.style.opacity=String(1-smooth(span(progress,.76,.96)));
    railFill.style.height=`${Math.round(progress*100)}%`;
    stage=progress < .32 ? 1 : progress < .79 ? 2 : 3;
    railCurrent.textContent=String(stage).padStart(2,'0');
    stageName.textContent=['','入境 · 启卷','寻名 · 展卷','考据 · 入卷'][stage];
    root.classList.toggle('is-light',progress>.78);
    topbar.classList.toggle('light-nav',progress>.82 || rect.bottom < window.innerHeight*.9);
    topbar.classList.toggle('dark-nav',rect.bottom > window.innerHeight*.9 && progress<=.82);
  }
  function queueScene(){ if(!requestId) requestId=requestAnimationFrame(updateScene); }
  window.addEventListener('scroll',queueScene,{passive:true});
  window.addEventListener('resize',queueScene,{passive:true});
  reduceMotion.addEventListener?.('change',queueScene);
  queueScene();

  // Optional context must be supplied by a verified person-ID lookup. A typed
  // search term never becomes a person record in this standalone visual lab.
  const person = window.GUJI_CONFIG?.person;
  if (person && typeof person.person_id === 'string' && person.person_id &&
      typeof person.canonical_name === 'string' && person.canonical_name) {
    $$('[data-person-name]').forEach(node => node.textContent = person.canonical_name);
    $$('[data-person-dynasty]').forEach(node => node.textContent = person.dynasty || '待考');
    $$('[data-person-courtesy]').forEach(node => node.textContent = person.courtesy || '待考');
    $$('[data-person-art]').forEach(node => node.textContent = person.art || '待考');
    $('.archive-number').textContent = `数字题签 · ${person.person_id}`;
    const aliasTags = $('[data-person-aliases]');
    const aliases = [person.courtesy && `字 ${person.courtesy}`, person.art && `号 ${person.art}`].filter(Boolean);
    if (aliasTags && aliases.length) {
      aliasTags.replaceChildren(...aliases.map(text => {
        const span = document.createElement('span');
        span.textContent = text;
        return span;
      }));
    }
  }

  // Real UI text/search layer -- no search result is fabricated in prototype mode.
  function search(query){
    const q=(query||'').trim();
    if(!q) { $('#hero-search').focus();return; }
    const template = window.GUJI_CONFIG?.searchUrlTemplate?.trim();
    if (template && template.includes('{query}')) {
      location.href=template.replaceAll('{query}',encodeURIComponent(q));
      return;
    }
    $('#dialog-query').textContent=q;
    $('#search-dialog').showModal();
  }
  $('#hero-search-form').addEventListener('submit',event=>{
    event.preventDefault();search($('#hero-search').value);
  });
  $$('.quick-search').forEach(button=>button.addEventListener('click',()=>{
    $('#hero-search').value=button.dataset.query;
    search(button.dataset.query);
  }));
  $('#try-form').addEventListener('click',()=>{
    window.scrollTo({top:0,behavior:reduceMotion.matches?'instant':'smooth'});
    setTimeout(()=>$('#hero-search').focus({preventScroll:true}),reduceMotion.matches?0:550);
  });
  // Click outside of modal to dismiss, support ESC through native dialog.
  $('#search-dialog').addEventListener('click',ev=>{
    if(ev.target===$('#search-dialog')) $('#search-dialog').close();
  });

  const tabs = $$('.tab');
  function activateTab(tab,focusTab=false){
    tabs.forEach(t=>{
      const selected=t===tab;
      t.classList.toggle('active',selected);
      t.setAttribute('aria-selected',String(selected));
      $('#panel-'+t.dataset.panel).hidden=!selected;
      t.tabIndex=selected?0:-1;
    });
    if(focusTab)tab.focus();
  }
  tabs.forEach((tab,i)=>{
    tab.addEventListener('click',()=>activateTab(tab));
    tab.addEventListener('keydown',ev=>{
      let j;
      if(ev.key==='ArrowRight') j=(i+1)%tabs.length;
      else if(ev.key==='ArrowLeft') j=(i-1+tabs.length)%tabs.length;
      else if(ev.key==='Home') j=0;
      else if(ev.key==='End') j=tabs.length-1;
      else return;
      ev.preventDefault();activateTab(tabs[j],true);
    });
  });
  menuButton.addEventListener('click',()=>{
    const open=menuButton.getAttribute('aria-expanded')!=='true';
    menuButton.setAttribute('aria-expanded',String(open));
    menuButton.setAttribute('aria-label',open?'关闭导航菜单':'打开导航菜单');
    menu.classList.toggle('open',open);
  });
  menu.querySelectorAll('a').forEach(a=>a.addEventListener('click',()=>{
    menu.classList.remove('open');menuButton.setAttribute('aria-expanded','false');
    menuButton.setAttribute('aria-label','打开导航菜单');
  }));
  // Native anchors + CSS scroll-margin; animation never blocks keyboard navigation.
})();
