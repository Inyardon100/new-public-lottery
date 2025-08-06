import streamlit as st
import sqlite3
import random
import time
import datetime
import pandas as pd
from streamlit_autorefresh import st_autorefresh
import hashlib # 비밀번호 해싱을 위해 추가

# --- 시간대 설정 (한국시간) ---
KST = datetime.timezone(datetime.timedelta(hours=9))

def now_kst():
    return datetime.datetime.now(KST)

# --- 1. 설정 및 데이터베이스 초기화 ---
def setup_database():
    conn = sqlite3.connect('lottery_data_v2.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("PRAGMA foreign_keys = ON;")
    c.execute('''
        CREATE TABLE IF NOT EXISTS lotteries (id INTEGER PRIMARY KEY, title TEXT NOT NULL, draw_time TIMESTAMP, num_winners INTEGER, status TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, password_hash TEXT)
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS participants (id INTEGER PRIMARY KEY, lottery_id INTEGER, name TEXT NOT NULL, FOREIGN KEY (lottery_id) REFERENCES lotteries (id) ON DELETE CASCADE)
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS winners (id INTEGER PRIMARY KEY, lottery_id INTEGER, winner_name TEXT, draw_round INTEGER, FOREIGN KEY (lottery_id) REFERENCES lotteries (id) ON DELETE CASCADE)
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS lottery_logs (id INTEGER PRIMARY KEY, lottery_id INTEGER, log_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, log_message TEXT, FOREIGN KEY (lottery_id) REFERENCES lotteries (id) ON DELETE CASCADE)
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS scheduled_redraws (
            id INTEGER PRIMARY KEY, lottery_id INTEGER NOT NULL, execution_time TIMESTAMP NOT NULL,
            num_winners INTEGER NOT NULL, candidates TEXT NOT NULL, FOREIGN KEY (lottery_id) REFERENCES lotteries(id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    return conn

# --- 2. 헬퍼 및 로직 함수 ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def add_log(conn, lottery_id, message):
    c = conn.cursor()
    c.execute("INSERT INTO lottery_logs (lottery_id, log_message, log_timestamp) VALUES (?, ?, ?)", (lottery_id, message, now_kst()))
    conn.commit()

def run_draw(conn, lottery_id, num_to_draw, candidates):
    actual = min(num_to_draw, len(candidates))
    if actual <= 0: return []
    winners = random.sample(candidates, k=actual)
    c = conn.cursor()
    c.execute("SELECT MAX(draw_round) FROM winners WHERE lottery_id = ?", (lottery_id,))
    prev = c.fetchone()[0] or 0
    current_round = prev + 1
    for w in winners:
        c.execute("INSERT INTO winners (lottery_id, winner_name, draw_round) VALUES (?, ?, ?)",(lottery_id, w, current_round))
    if current_round == 1:
        c.execute("UPDATE lotteries SET status = 'completed' WHERE id = ?", (lottery_id,))
    conn.commit()
    add_log(conn, lottery_id, f"{current_round}회차 추첨 진행. (당첨자: {', '.join(winners)})")
    return winners

def check_and_run_scheduled_draws(conn):
    c = conn.cursor()
    now = now_kst()
    c.execute("SELECT id, num_winners FROM lotteries WHERE status = 'scheduled' AND draw_time <= ?", (now,))
    for lottery_id, num_winners in c.fetchall():
        c.execute("SELECT name FROM participants WHERE lottery_id = ?", (lottery_id,))
        participants = [r[0] for r in c.fetchall()]
        if participants:
            winners = run_draw(conn, lottery_id, num_winners, participants)
            if winners: st.session_state[f'celebrated_{lottery_id}'] = True

def check_and_run_scheduled_redraws(conn):
    c = conn.cursor()
    now = now_kst()
    c.execute("SELECT id, lottery_id, num_winners, candidates FROM scheduled_redraws WHERE execution_time <= ?", (now,))
    tasks_to_run = c.fetchall()
    for task_id, lottery_id, num_winners, candidates_str in tasks_to_run:
        candidates = candidates_str.split(',')
        if candidates:
            winners = run_draw(conn, lottery_id, num_winners, candidates)
            if winners: st.session_state[f'celebrated_{lottery_id}'] = True
        c.execute("DELETE FROM scheduled_redraws WHERE id = ?", (task_id,)); conn.commit()

# --- 3. Streamlit UI 구성 ---
def main():
    st.set_page_config(page_title="new lottery", page_icon="📜", layout="wide")
    st_autorefresh(interval=1000, limit=None, key="main_refresher")
    conn = setup_database()
    check_and_run_scheduled_draws(conn)
    check_and_run_scheduled_redraws(conn)

    st.session_state.setdefault('super_admin_auth', False)
    st.session_state.setdefault('creator_auth', {})
    st.session_state.setdefault('view_mode', 'list')
    st.session_state.setdefault('selected_lottery_id', None)

    st.title("📜 NEW LOTTERY")
    with st.expander("🔑 슈퍼 관리자 로그인"):
        if not st.session_state.super_admin_auth:
            super_pw = st.text_input("슈퍼 관리자 인증키", type="password", key="super_admin_pw")
            if st.button("인증", key="super_admin_auth_btn"):
                if super_pw == st.secrets.get("super_admin", {}).get("password"):
                    st.session_state.super_admin_auth = True; st.experimental_rerun()
                else:
                    st.error("인증키가 올바르지 않습니다.")
        else:
            st.success("슈퍼 관리자로 인증되었습니다.")

    st.markdown("---")
    col1, col2 = st.columns([2, 1])

    with col1:
        if st.session_state.view_mode == 'detail' and st.session_state.selected_lottery_id is not None:
            if st.button("🔙 목록으로 돌아가기"):
                st.session_state.view_mode = 'list'; st.session_state.selected_lottery_id = None; st.experimental_rerun()
            
            lid = st.session_state.selected_lottery_id
            try:
                sel_row = pd.read_sql("SELECT * FROM lotteries WHERE id = ?", conn, params=(lid,)).iloc[0]
                title, status, raw_draw_time, pw_hash = sel_row['title'], sel_row['status'], sel_row['draw_time'], sel_row['password_hash']
                
                if isinstance(raw_draw_time, str): draw_time = datetime.datetime.fromisoformat(raw_draw_time)
                else: draw_time = raw_draw_time
                if hasattr(draw_time, 'tzinfo') and draw_time.tzinfo is None: draw_time = draw_time.replace(tzinfo=KST)

                with st.container(border=True):
                    st.header(f"✨ {title}")
                    if status == 'completed':
                        st.success(f"**추첨 완료!** ({draw_time.strftime('%Y-%m-%d %H:%M:%S %Z')})")
                        winners_df = pd.read_sql("SELECT winner_name, draw_round FROM winners WHERE lottery_id = ? ORDER BY draw_round", conn, params=(lid,))
                        for rnd, grp in winners_df.groupby('draw_round'):
                            label = '1회차' if rnd == 1 else f"{rnd}회차 (재추첨)"
                            st.markdown(f"#### 🏆 {label} 당첨자")
                            tags = " &nbsp; ".join([f"<span style='background-color:#E8F5E9; color:#1E8E3E; border-radius:5px; padding:5px 10px; font-weight:bold;'>{n}</span>" for n in grp['winner_name']])
                            st.markdown(f"<p style='text-align:center; font-size:20px;'>{tags}</p>", unsafe_allow_html=True)
                        if st.session_state.get(f'celebrated_{lid}', False):
                            st.balloons(); st.session_state[f'celebrated_{lid}'] = False
                    else:
                        diff = draw_time - now_kst()
                        if diff.total_seconds() > 0: st.info(f"**추첨 예정:** {draw_time.strftime('%Y-%m-%d %H:%M:%S %Z')} (남은 시간: {str(diff).split('.')[0]})")
                        else: st.warning("예정 시간이 지났습니다. 곧 자동 진행됩니다...")
                    
                    redraw_tasks = pd.read_sql("SELECT execution_time, num_winners FROM scheduled_redraws WHERE lottery_id=?", conn, params=(lid,))
                    for _, task in redraw_tasks.iterrows():
                        rt = task['execution_time']
                        if isinstance(rt, str): rt = datetime.datetime.fromisoformat(rt)
                        if hasattr(rt, 'tzinfo') and rt.tzinfo is None: rt = rt.replace(tzinfo=KST)
                        st.info(f"**재추첨 예약됨:** {rt.strftime('%Y-%m-%d %H:%M:%S')} ({task['num_winners']}명)")
                    
                    tabs = st.tabs(["참가자 명단", "📜 추첨 로그", "👑 관리"])
                    with tabs[0]:
                        part_df = pd.read_sql("SELECT name FROM participants WHERE lottery_id = ?", conn, params=(lid,))
                        st.dataframe(part_df.rename(columns={'name':'이름'}), use_container_width=True, height=200)
                    with tabs[1]:
                        log_df = pd.read_sql("SELECT strftime('%Y-%m-%d %H:%M:%S', log_timestamp, 'localtime') AS 시간, log_message AS 내용 FROM lottery_logs WHERE lottery_id = ? ORDER BY id", conn, params=(lid,))
                        st.dataframe(log_df, use_container_width=True, height=200)
                    with tabs[2]:
                        st.subheader("추첨 관리")
                        if st.session_state.super_admin_auth:
                            st.write("**슈퍼 관리자 기능: 추첨 삭제**")
                            if st.button("이 추첨 영구 삭제", key=f"super_delete_btn_{lid}", type="primary"):
                                st.session_state.delete_confirm_id = lid
                            if st.session_state.get('delete_confirm_id') == lid:
                                st.warning(f"'{title}' 추첨의 모든 기록이 영구적으로 삭제됩니다. 계속하시겠습니까?")
                                if st.button("예, 정말로 삭제합니다", key=f"super_confirm_delete_{lid}"):
                                    c = conn.cursor(); c.execute("DELETE FROM lotteries WHERE id=?", (lid,)); conn.commit()
                                    st.session_state.view_mode = 'list'; st.session_state.selected_lottery_id = None
                                    st.success("삭제 완료"); time.sleep(1); st.experimental_rerun()
                        
                        if status == 'completed':
                            st.write("**생성자 기능: 재추첨**")
                            if not st.session_state.creator_auth.get(lid, False):
                                creator_pw = st.text_input("추첨 비밀번호 입력", type="password", key=f"creator_pw_{lid}")
                                if st.button("재추첨 권한 인증", key=f"creator_auth_btn_{lid}"):
                                    if hash_password(creator_pw) == pw_hash:
                                        st.session_state.creator_auth[lid] = True; st.experimental_rerun()
                                    else: st.error("비밀번호가 일치하지 않습니다.")
                            else:
                                st.success("관리 권한이 인증되었습니다.")
                                all_p = pd.read_sql("SELECT name FROM participants WHERE lottery_id=?", conn, params=(lid,))['name'].tolist()
                                prev = pd.read_sql("SELECT winner_name FROM winners WHERE lottery_id=?", conn, params=(lid,))['winner_name'].tolist()
                                cand = list(all_p)
                                for winner in prev:
                                    if winner in cand: cand.remove(winner)
                                if cand:
                                    redraw_type = st.radio("재추첨 방식", ["즉시 추첨", "예약 추첨"], key=f"detail_redraw_type_{lid}", horizontal=True)
                                    redraw_time = now_kst()
                                    if redraw_type == "예약 추첨":
                                        date = st.date_input("날짜", value=now_kst().date(), key=f"detail_redraw_date_{lid}")
                                        default_tm = st.session_state.get(f'detail_redraw_time_{lid}', (now_kst() + datetime.timedelta(minutes=5)).time())
                                        tm = st.time_input("시간", value=default_tm, key=f"detail_redraw_time_{lid}", step=datetime.timedelta(minutes=1))
                                        redraw_time = datetime.datetime.combine(date, tm, tzinfo=KST)
                                    chosen = st.multiselect("재추첨 후보자", cand, default=cand, key=f"detail_redraw_cand_{lid}")
                                    num_r = st.number_input("추첨 인원", 1, len(chosen) if chosen else 1, 1, key=f"detail_redraw_num_{lid}")
                                    if st.button("🚀 재추첨 실행/예약", key=f"detail_redraw_btn_{lid}", type="primary"):
                                        if not chosen: st.warning("후보자를 선택하세요.")
                                        elif redraw_type == "예약 추첨" and redraw_time <= now_kst(): st.error("예약 시간은 현재 이후여야 합니다.")
                                        else:
                                            if redraw_type == "즉시 추첨":
                                                run_draw(conn, lid, num_r, chosen); st.success("재추첨 완료"); time.sleep(1); st.experimental_rerun()
                                            else:
                                                c = conn.cursor(); candidates_str = ",".join(chosen)
                                                c.execute("INSERT INTO scheduled_redraws (lottery_id, execution_time, num_winners, candidates) VALUES (?, ?, ?, ?)", (lid, redraw_time, num_r, candidates_str))
                                                conn.commit(); add_log(conn, lid, f"재추첨 예약됨 ({len(chosen)}명 대상)")
                                                st.success("재추첨이 예약되었습니다."); time.sleep(1); st.experimental_rerun()
                                else: st.warning("재추첨 후보가 없습니다.")
                        else: st.info("완료된 추첨만 재추첨할 수 있습니다.")

            except (IndexError, pd.errors.EmptyDataError):
                 st.error("추첨을 찾을 수 없습니다."); st.session_state.view_mode = 'list'
        
        else: # 목록 보기
            st.header("🎉 추첨 목록")
            df_lot = pd.read_sql("SELECT id, title, status FROM lotteries ORDER BY id DESC", conn)
            if df_lot.empty: st.info("아직 생성된 추첨이 없습니다.")
            else:
                for _, row in df_lot.iterrows():
                    with st.container(border=True):
                        list_col1, list_col2, list_col3 = st.columns([5, 2, 2])
                        status_emoji = "🟢 진행중" if row['status'] == 'scheduled' else "🏁 완료"
                        with list_col1: st.write(f"#### {row['title']}")
                        with list_col2: st.markdown(f"**{status_emoji}**")
                        with list_col3:
                            if st.button("상세보기", key=f"detail_btn_{row['id']}"):
                                st.session_state.view_mode = 'detail'; st.session_state.selected_lottery_id = int(row['id']); st.experimental_rerun()

    with col2:
        st.header("🖋️ 새 추첨 생성")
        title = st.text_input("추첨 제목", key="new_title")
        password = st.text_input("추첨 관리 비밀번호", type="password", key="new_password")
        num_winners = st.number_input("당첨 인원 수", 1, 1, key="new_num_winners")
        draw_type = st.radio("추첨 방식", ["즉시 추첨", "예약 추첨"], key="new_draw_type", horizontal=True)
        if draw_type == "예약 추첨":
            date = st.date_input("날짜", value=now_kst().date(), key="new_draw_date")
            default_tm = st.session_state.get('new_draw_time', (now_kst() + datetime.timedelta(minutes=5)).time())
            tm = st.time_input("시간 (HH:MM)", value=default_tm, key="new_draw_time", step=datetime.timedelta(minutes=1))
            draw_time = datetime.datetime.combine(date, tm, tzinfo=KST)
        else: draw_time = now_kst()
        participants_txt = st.text_area("참가자 명단 (한 줄에 한 명)", key="new_participants", height=150)
        if st.button("✅ 추첨 생성", key="create_button", type="primary"):
            names = [n.strip() for n in participants_txt.split('\n') if n.strip()]
            if not title or not names or not password: st.warning("제목, 비밀번호, 참가자를 모두 입력하세요.")
            elif draw_type == "예약 추첨" and draw_time <= now_kst(): st.error("예약 시간은 현재 이후여야 합니다.")
            else:
                hashed_password = hash_password(password)
                c = conn.cursor()
                c.execute("INSERT INTO lotteries (title, draw_time, num_winners, status, password_hash) VALUES (?, ?, ?, 'scheduled', ?)", (title, draw_time, num_winners, hashed_password))
                lid = c.lastrowid
                for n in names: c.execute("INSERT INTO participants (lottery_id, name) VALUES (?, ?)", (lid, n))
                conn.commit()
                add_log(conn, lid, f"추첨 생성됨 (방식: {draw_type})")
                st.success("추첨 생성 완료"); time.sleep(1); st.experimental_rerun()
    conn.close()

if __name__ == "__main__":
    main()
