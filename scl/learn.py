from .config import config

def learn(prompt, cap_registry):
    ### for in ....
    ### it will test history messages, to ensure that pass@k, pass^k work
    new_similarity=0.3
    ### for  in ...
    cap_registry.search_by_similarity(msg, limit=5, min_similarity=new_similarity)
    ### if ...
    ### continue
    config.min_similarity=new_similarity
    ### after learn, the history been reset.
    cap_registry.clean_history()
