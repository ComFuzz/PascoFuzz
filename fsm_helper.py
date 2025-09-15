from objects import *
import random

# Convert dot file to FSM
def get_states_and_tx(filename):
    states = []
    transitions = []

    with open(filename, "r") as f:
        lines = f.readlines()
        for i in range(len(lines)):
            if 'shape' in lines[i]:
                strg = lines[i].split('[')[0].strip()
                states.append(strg.strip())

            elif '//' in lines[i] and lines[i].startswith('//'):
                continue

            elif "__start0" in lines[i]:
                continue

            elif '->' in lines[i]:
                transition = ''
                strg = lines[i].split('->')
                start_state = strg[0].strip()
                strg = strg[1].split('[')
                end_state = strg[0].strip()

                if start_state not in states:
                    print('ERROR: start_state is not in the list of states')
                    return

                if end_state not in states:
                    print('ERROR: end_state is not in the list of states')
                    return

                strg = strg[1].split('"')
                if len(strg) == 3:  # transition is written in one line
                    transition = strg[1]

                values = transition.split('/')
                # print 'values = ', values

                input_sym = values[0].strip()
                output_sym = values[1].strip()
                transitions.append([start_state, input_sym, output_sym, end_state])

    states.remove("__start0")

    return states, transitions, states[0]

# create FSM from dot file
def load_fsm(filename):
    states_file, transitions, init_state = get_states_and_tx(filename)
    states = []
    for state in states_file:
        states.append(State(state, [])) # check if able to add path here
    fsm = FSM(states, init_state, transitions)
    for state in fsm.states:
        get_all_paths(fsm, state) # get all paths for each state
        state.oracle.decide_state(state) # calculate the security state of each state
        print("state:", state.oracle.state)
    return fsm

def get_trace_from_path(fsm: FSM, path: list):
    # this function will return 2 lists an input trace and an output trace for a path
    input_trace = []
    output_trace = []
    for i in range(len(path)-1): # each round get one deviant trace and append
        state1_num = path[i]
        state2_num = path[i+1] # each time we get 2 states out and search them in the list, see what's the input/output
        if state1_num != state2_num:
            state1 = str(state1_num)
            state2 = str(state2_num)
            deviant_input_list = []
            deviant_output_list = []
            for transitions in fsm.transitions:
                if transitions[0] == state1 and transitions[3] == state2:
                    deviant_input_list.append(transitions[1])
                    deviant_output_list.append(transitions[2])
            index = random.randint(1, len(deviant_input_list)) - 1
            input_trace.append(deviant_input_list[index])
            output_trace.append(deviant_output_list[index])
        
    return input_trace, output_trace


# calculate all path to a state
def get_all_paths(fsm: FSM, dst_state: State):

    if fsm.init_state == dst_state.name:
        return None

    # start creating the graph
    graph = Graph(len(fsm.states), vertices_names=fsm.get_state_names())
    # Convert FSM to graph
    for item in fsm.transitions:
        Source_state = item[0]
        Dest_state = item[3]
        if Source_state != Dest_state:
            graph_u = graph.getgraph(Source_state)
            if Dest_state in graph_u:
                pass
            else:
                graph.addEdge(Source_state, Dest_state)

    all_paths = []
    graph.printAllPaths(fsm.init_state, dst_state.name, all_paths)
    all_paths.sort(key=len)

    # check problematic paths
    temp_paths = []
    for path in all_paths:
        if len(path) <= graph.V:
            temp_paths.append(path)
        else:
            print("ERROR: PATH LARGER THAN NUMBER OF STATES!!!")
            print(path)

    all_paths = temp_paths

    for path in all_paths:
        if dst_state.is_existed_path(path):
            pass
        else:
            input, output = get_trace_from_path(fsm, path)
            dst_state.add_path(Path(path, input, output))
    
    # for path in dst_state.paths:
    #     print("path:", path.path_states)
    #     print("input:", path.input_symbols)
    #     print("output:", path.output_symbols)