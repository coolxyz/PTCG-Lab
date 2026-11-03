// Synthetic HTTPS/WSS protocol peer. No real identities, database or rule execution.
import https from 'node:https';
import fs from 'node:fs';
import crypto from 'node:crypto';
const input=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const rows=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const wire=kind=>structuredClone(rows.find(r=>r.kind===kind).value);
const mid='a'.repeat(32),roomId='c'.repeat(32),prefix='/api/online/v2';
let sessions=0,version=1,ready=[false,false],seq=3,epoch=0,done=false,dropped=false,commands=0,upgrades=0,originHeaders=0,cookieHeaders=0,redirectFollowed=0;
const receipts=new Map();
function seat(req){return req.headers.authorization===`Bearer atk_${'b'.repeat(43)}`?'PLAYER2':'PLAYER1';}
function snapshot(who){const v=rows.filter(r=>r.kind==='MatchSnapshot').find(r=>r.value.viewerSeat===who).value;const out=structuredClone(v);Object.assign(out,{matchId:mid,streamCursor:(seq===3?'0':'1').repeat(32),matchSeq:seq,controllerEpoch:epoch,done,status:done?'finished':'playing',phase:done?'finished':'playing',winner:done?'PLAYER2':null});if(done)out.decision=null;return out;}
function room(req){return {protocolVersion:'2.0',roomId,roomVersion:version,inviteCode:'ABCDEFGH23',ruleReleaseId:'synthetic-rules-1',status:ready.every(Boolean)?'initialized':'waiting',expiresAt:'2099-01-01T00:00:00Z',viewerSeat:seat(req),deckRevision:(seat(req)==='PLAYER1'?'1':'2').repeat(32),members:[{seat:'PLAYER1',ready:ready[0]},...(sessions>=2?[{seat:'PLAYER2',ready:ready[1]}]:[])],matchId:ready.every(Boolean)?mid:null};}
function send(res,value,status=200){res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));}
const server=https.createServer({key:fs.readFileSync(input.key),cert:fs.readFileSync(input.cert)},(req,res)=>{
  originHeaders+=Number(!!req.headers.origin);cookieHeaders+=Number(!!req.headers.cookie);
  if(req.url===prefix+'/unexpected-redirect'){redirectFollowed++;send(res,{},500);return;}
  if(input.redirect&&req.url===prefix+'/capabilities'){res.writeHead(302,{Location:prefix+'/unexpected-redirect'});res.end();return;}
  let raw='';req.on('data',chunk=>raw+=chunk);req.on('end',()=>{
    const body=raw?JSON.parse(raw):{};const path=req.url.slice(prefix.length);
    if(path==='/capabilities'){send(res,wire('Capabilities'));return;}
    if(path==='/sessions'){sessions++;send(res,{protocolVersion:'2.0',serverId:'synthetic-peer',userId:String(sessions).repeat(32),deviceId:String(sessions+2).repeat(32),sessionId:String(sessions+4).repeat(32),tokenType:'Bearer',accessToken:`atk_${(sessions===1?'a':'b').repeat(43)}`,refreshToken:`rtk_${(sessions===1?'a':'b').repeat(43)}`,accessExpiresAt:'2099-01-01T00:00:00Z',refreshExpiresAt:'2099-02-01T00:00:00Z'});return;}
    if(!req.headers.authorization){send(res,{protocolVersion:'2.0',code:'UNAUTHORIZED',retryable:false},401);return;}
    if(path==='/decks'){send(res,{protocolVersion:'2.0',deckRevision:(seat(req)==='PLAYER1'?'1':'2').repeat(32),ruleReleaseId:body.ruleReleaseId,formatAsOf:'2026-10-31'});return;}
    if(path==='/rooms'){send(res,room(req));return;}
    if(path==='/rooms/join'){version++;send(res,room(req));return;}
    if(path===`/rooms/${roomId}/ready`){ready[seat(req)==='PLAYER1'?0:1]=body.ready;version++;send(res,room(req));return;}
    if(path===`/matches/${mid}`){send(res,snapshot(seat(req)));return;}
    if(path===`/matches/${mid}/control`){epoch++;send(res,snapshot(seat(req)));return;}
    if(path.includes('/receipts/')){const value=receipts.get(path.split('/').at(-1));send(res,value||{protocolVersion:'2.0',code:'MATCH_NOT_FOUND',retryable:false},value?200:404);return;}
    if(path.endsWith('/commands')||path.endsWith('/resign')){commands++;seq++;if(path.endsWith('/resign'))done=true;const receipt={protocolVersion:'2.0',matchId:mid,commandId:body.commandId,accepted:true,matchSeq:seq,stateVersion:2,streamCursor:'1'.repeat(32)};receipts.set(body.commandId,receipt);if(!dropped){dropped=true;req.socket.destroy();return;}send(res,receipt);return;}
    if(path.endsWith('/frames')){const page=wire('ReplayPage');page.matchId=mid;page.viewerSeat=seat(req);const v=snapshot(seat(req));for(const key of Object.keys(page.frames[0]))if(key in v)page.frames[0][key]=v[key];send(res,page);return;}
    send(res,{protocolVersion:'2.0',code:'MATCH_NOT_FOUND',retryable:false},404);
  });
});
function frame(value){const body=Buffer.from(JSON.stringify(value));const header=body.length<126?Buffer.from([0x81,body.length]):Buffer.from([0x81,126,body.length>>8,body.length&255]);return Buffer.concat([header,body]);}
server.on('upgrade',(req,socket)=>{
  upgrades++;originHeaders+=Number(!!req.headers.origin);cookieHeaders+=Number(!!req.headers.cookie);
  const accept=crypto.createHash('sha1').update(req.headers['sec-websocket-key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
  socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
  let bytes=Buffer.alloc(0);socket.on('data',chunk=>{bytes=Buffer.concat([bytes,chunk]);if(bytes.length<6)return;const op=bytes[0]&15;if(op===8){socket.end();return;}let size=bytes[1]&127,offset=2;if(size===126){if(bytes.length<8)return;size=bytes.readUInt16BE(2);offset=4;}if(size===127){socket.destroy();return;}if(bytes.length<offset+4+size)return;const mask=bytes.subarray(offset,offset+4),body=Buffer.from(bytes.subarray(offset+4,offset+4+size));for(let i=0;i<body.length;i++)body[i]^=mask[i%4];const sub=JSON.parse(body);if(sub.matchId!==mid){socket.destroy();return;}socket.write(frame({protocolVersion:'2.0',type:'snapshot',snapshot:snapshot(seat(req))}));bytes=Buffer.alloc(0);});socket.on('error',()=>{});
});
server.on('tlsClientError',()=>{});
server.listen(0,'127.0.0.1',()=>console.log(JSON.stringify({origin:`https://127.0.0.1:${server.address().port}`})));
process.on('SIGTERM',()=>{fs.writeFileSync(input.report,JSON.stringify({sessions,commands,upgrades,originHeaders,cookieHeaders,redirectFollowed}));server.close();setTimeout(()=>process.exit(0),50).unref();});
