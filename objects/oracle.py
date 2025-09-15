# path: Path; state: State
from statistics import mode
class Oracle:
    def __init__(self) -> None:
        # I: Initial State, N: No Security Context, S: Security Context, R: Registered, D: Deregistered, O: Other
        self.state = "I"
        self.allowed_plaintext = ["registrationRequest",
                                  "deregistrationRequest",
                                  "securityModeReject",
                                  "authenticationRequest",
                                  "authenticationResponse",
                                  "authenticationFailure",
                                  "deregistrationAccept",
                                  "identityResponse",
                                  "gmmStatus",
                                  "ulNasTransport"]

    # check if the security header type and encryption algorithm match
    def check_security(self, send_type: str, sht: int, secmod: int) -> bool:
        if sht < 5:
            if sht+1 == secmod:
                return True
            elif sht == 4 and secmod == 3 and send_type == "securityModeComplete": # SECURITY MODE COMPLETE
                return True
        return False
    
    def decide_state(self, fsm_state) -> None:
        states = []
        if fsm_state.paths == []: # initial state
            self.state = "I"
            return
        for path in fsm_state.paths:
            states.append(self.find_state_rec(path, "I", 0))

        # self.state = states[0]
        
        if states.count(states[0]) == len(states): # check if all path have the same state
            self.state = states[0]
        else:
            # Find the most common state for all paths
            # self.state = mode(states)
            self.state = "O"
            # with open("log.txt", "a") as f:
            #     f.write("state name: " + fsm_state.name + "\n")
            #     for i in range(len(states)):
            #         f.write("input: " + str(fsm_state.paths[i].input_symbols) + "\n")
            #         f.write("output: " + str(fsm_state.paths[i].output_symbols) + "\n")
            #         f.write("state: " + str(states[i]) + "\n")
            # print("states:", states)
            # raise Exception("Oracle: Diverge path states!")
        
    def find_state_rec(self, path, state, index) -> str:
        size = len(path.input_symbols)
        # print("size:", size)
        if state == "I": # I -> N / I -> D
            for i in range(index, size):
                if "registrationRequest" in path.input_symbols[i] and "deregistrationRequest" not in path.input_symbols[i] and path.output_symbols[i] != "registrationReject" and path.output_symbols[i] != "null_action":                    
                    state = self.find_state_rec(path, "N", i)
                    # print("I -> N: ", i)
                    break
                # error transition for Open5GS
                elif "identityResponse" in path.input_symbols[i] and path.output_symbols[i] == "authenticationRequest":
                    state = self.find_state_rec(path, "N", i)
                    # print("I -> N: ", i)
                    break
                elif "serviceRequest" in path.input_symbols[i] and path.output_symbols[i] == "serviceReject":
                    state = self.find_state_rec(path, "D", i)
                    # print("I -> D: ", i)
                    break
        elif state == "N": # N -> S / N -> D
            for i in range(index, size):
                if i+1 != size and path.output_symbols[i] == "securityModeCommand" and "securityModeComplete" in path.input_symbols[i+1]:
                    state = self.find_state_rec(path, "S", i+1)
                    # print("N -> S: ", i)
                    break
                elif "deregistrationRequest" in path.input_symbols[i] and path.output_symbols[i] == "deregistrationAccept":
                    state = self.find_state_rec(path, "D", i)
                    # print("N -> D: ", i)
                    break
                elif "serviceRequest" in path.input_symbols[i] and path.output_symbols[i] == "serviceReject":
                    state = self.find_state_rec(path, "D", i)
                    # print("N -> D: ", i)
                    break
        elif state == "S": # S -> R / S -> D
            for i in range(index, size):
                if i+1 != size and path.output_symbols[i] == "registrationAccept" and "registrationComplete" in path.input_symbols[i+1]:
                    state = self.find_state_rec(path, "R", i+1)
                    # print("S -> R: ", i)
                    break
                elif "deregistrationRequest" in path.input_symbols[i] and path.output_symbols[i] == "deregistrationAccept":
                    state = self.find_state_rec(path, "D", i)
                    # print("S -> D: ", i)
                    break
                elif "serviceRequest" in path.input_symbols[i] and path.output_symbols[i] == "serviceReject":
                    state = self.find_state_rec(path, "D", i)
                    # print("S -> D: ", i)
                    break
        elif state == "R": # R -> D / R -> S
            for i in range(index, size):
                if "deregistrationRequest" in path.input_symbols[i] and path.output_symbols[i] == "deregistrationAccept":
                    state = self.find_state_rec(path, "D", i)
                    # print("R -> D: ", i)
                    break
                elif "serviceRequest" in path.input_symbols[i] and path.output_symbols[i] == "serviceReject":
                    state = self.find_state_rec(path, "D", i)
                    # print("R -> D: ", i)
                    break
                elif "registrationRequest" in path.input_symbols[i] and "deregistrationRequest" not in path.input_symbols[i] and path.output_symbols[i] != "null_action":
                    state = self.find_state_rec(path, "S", i)
                    # print("R -> S: ", i)
                    break
                # error transition for Open5GS
                elif "identityResponse" in path.input_symbols[i] and path.output_symbols[i] == "authenticationRequest":
                    state = self.find_state_rec(path, "S", i)
                    # print("R -> S: ", i)
                    break
                elif i+1 != size and path.output_symbols[i] == "securityModeCommand" and "securityModeComplete" in path.input_symbols[i+1]:
                    state = self.find_state_rec(path, "S", i+1)
                    # print("R -> S: ", i)
                    break
        elif state == "D": # D -> S / D -> R
            for i in range(index, size):
                if "registrationRequest" in path.input_symbols[i] and "deregistrationRequest" not in path.input_symbols[i]:
                    if path.output_symbols[i] == "registrationAccept":
                        state = self.find_state_rec(path, "R", i)
                        # print("D -> R: ", i)
                        break
                    elif path.output_symbols[i] != "registrationReject" and path.output_symbols[i] != "null_action":                    
                        state = self.find_state_rec(path, "N", i)
                        # print("D -> S: ", i)
                        break
                # error transition for Open5GS
                elif "identityResponse" in path.input_symbols[i] and path.output_symbols[i] == "authenticationRequest":
                    state = self.find_state_rec(path, "N", i)
                    # print("D -> S: ", i)
                    break
        return state

    # if True, the Core have a potential protocol violation
    def query_message(self, send_type: str, ret_type: str, sht: int, secmod: int) -> bool:
        # if no response, return False
        if ret_type == "" or ret_type == "gmmStatus":
            return False
        # check if sht and secmod match
        if not self.check_security(send_type, sht, secmod):
            return True
        # check if the message is allowed in each state
        if self.state == "I":
            if sht == 0:
                if send_type == "registrationRequest":
                    return False
                elif send_type == "deregistrationRequest":
                    return False
                elif send_type == "serviceRequest" and ret_type == "serviceReject":
                    return False
            else:
                return True
        elif self.state == "N" or "O":
            if sht == 0 and send_type in self.allowed_plaintext:
                return False
            elif sht == 4 and send_type == "securityModeComplete": # SECURITY MODE COMPLETE
                return False
            else:
                return True
        elif self.state == "S":
            if sht == 2:
                if send_type == "serviceRequest" and ret_type != "serviceReject":
                    return True
                return False
            elif sht == 4 and send_type == "securityModeComplete": # SECURITY MODE COMPLETE
                return False
            else:
                return True
        elif self.state == "R":
            # Can estiblish PDU session 
            if sht == 2:
                return False
            elif sht == 4 and send_type == "securityModeComplete": # SECURITY MODE COMPLETE
                return False
            else:
                return True
        elif self.state == "D":
            if sht == 0 or sht == 2:
                if send_type == "registrationRequest":
                    return False
                elif send_type == "deregistrationRequest":
                    return False
                elif send_type == "serviceRequest" and ret_type == "serviceReject":
                    return False
            else:
                return True
        else:
            raise Exception("Oracle: Invalid state!")
