"""Product-only extension; leaves frozen packages/battle and LLM experiments intact."""
import json
from packages.battle.service import MatchService as BaseService, runtime, h2
from apps.api.expert_policies import registry

class MatchService(BaseService):
    def _frame(self,db,id,game,event,status):
        provenance=game.config.get('provenance',{})
        if game.version==0 and provenance.get('aiVersion')==h2.VERSION:
            # Runs inside BaseService.create's existing write transaction, before ACK.
            row=db.execute('SELECT opponent FROM matches WHERE id=?',(id,)).fetchone()
            key=row['opponent']
            if key.startswith('revision:'):
                entries=self._admit(db,key[len('revision:'):])['entries']
            else:
                from packages.battle.presets import PRESETS
                from packages.collection.domain import RAW
                entries=next(t['entries'] for t in PRESETS+RAW['templates'] if t['id']==key)
            pin=registry.select(entries,self.path)
            if pin:
                provenance['h2Expert']=pin
                db.execute('UPDATE matches SET body=? WHERE id=?',(json.dumps(game.export_private_replay()),id))
        v=super()._frame(db,id,game,event,status)
        if provenance.get('aiVersion')==h2.VERSION:
            v['aiStrategy']=registry.public(provenance.get('h2Expert'))
            db.execute('UPDATE match_frames SET body=? WHERE match_id=? AND seq=?',(json.dumps(v),id,game.version))
        return v

    def _game(self,row):
        record=json.loads(row['body']);pin=record['config'].get('provenance',{}).get('h2Expert')
        if pin:registry.validate(pin,record['config']['deck2'])
        return super()._game(row)

    def _advance_h2(self, owner, id):
        """Compute outside the authority lock; stale or duplicate proposals never commit."""
        import copy
        from packages.simulation.a2 import legal
        from packages.battle.service import require

        rt = runtime()
        with self.lock, self.db() as db:
            row = self._row(db, owner, id)
            if row["status"] != "playing":
                return self._snapshot(db, row)
            game = self._game(row)
            if game.actor != rt.PlayerId.PLAYER2:
                return self._snapshot(db, row)
            sequence = game.version
            before = copy.deepcopy(rt.view(game, rt.PlayerId.PLAYER2))
            pin = game.config.get("provenance", {}).get("h2Expert")
        command, detail = registry.decide(before, pin) if pin else h2.decide(before)
        with self.lock, self.db(True) as db:
            row = self._row(db, owner, id)
            if row["status"] != "playing" or row["seq"] != sequence:
                return self._snapshot(db, row)
            game = self._game(row)
            current = rt.view(game, rt.PlayerId.PLAYER2)
            require(legal(current, command), "INVALID_AI_PROPOSAL", "AI 提案已失效", 409)
            try:
                game.submit(rt.PlayerId.PLAYER2, command)
                event = self._event(current, command, True)
                event.update(detail)
                event["explanation"] = "H2 根据可见信息选择动作；异常时使用基础策略降级"
                if pin:
                    event["explanation"] = registry.public(pin)["name"] + " 根据可见信息行动；异常时回退原 H2"
                self._save(db, row, game, event)
                return self._snapshot(db, self._row(db, owner, id))
            except Exception:
                self.cache.pop(id, None)
                raise

